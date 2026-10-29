"""Document upload and processing endpoints."""
import os, uuid, logging, json
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError, ObjectDeletedError
from typing import List

from app.db.database import get_db
from app.models.document import Document, DocumentType
from app.models.application import Application, GovVerification
from app.models.user import User
from app.schemas.document import DocumentOut
from app.core.security import get_current_user
from app.core.config import settings
from app.services.preprocessing import preprocess_image
from app.services.ocr_service import extract_text, run_multipass_ocr
from app.services.nlp_service import extract_information
from app.services.classification_service import classify_document
from app.services.gemini_vision_service import (
    should_use_vision_fallback,
    extract_aadhaar_address_via_gemini,
)
from app.services.document_validation import validate_required_documents
from app.services.storage_service import get_storage_service, generate_object_key, StorageError

router = APIRouter()
logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
MAX_BYTES = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024


from typing import List, Optional


def authorize_application_access(application: Application, current_user: User) -> None:
    """
    Validate that the authenticated user is authorized to access the given application.
    Admins may access all applications.
    Loan officers may only access applications assigned to them (application.user_id == current_user.id).
    All other roles or unassigned loan officers are denied with HTTP 403.
    """
    role_val = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    if role_val == "admin":
        return
    if role_val == "loan_officer" and application.user_id == current_user.id:
        return
    raise HTTPException(status_code=403, detail="Access denied")


@router.post("/upload", response_model=DocumentOut, status_code=201)
async def upload_document(
    application_id: int = Form(...),
    document_type: str = Form(...),
    joint_applicant_index: Optional[int] = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload a single document for an application."""
    # Validate application ownership and access
    app = db.query(Application).filter(Application.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    authorize_application_access(app, current_user)

    # Validate file extension
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"File type {ext} not allowed")

    # Read and size-check
    contents = await file.read()
    if len(contents) > MAX_BYTES:
        raise HTTPException(status_code=400, detail="File too large")

    # Storage integration: Supabase private bucket or Local fallback
    storage = get_storage_service()
    is_supabase = getattr(settings, "STORAGE_PROVIDER", "local").lower() == "supabase"

    if is_supabase:
        object_key = generate_object_key(
            category="documents",
            application_id=application_id,
            original_filename=file.filename or f"document{ext}",
        )
        try:
            file_path = storage.upload_file(
                bucket=settings.SUPABASE_DOCUMENTS_BUCKET,
                object_key=object_key,
                data=contents,
                content_type=file.content_type,
            )
        except Exception as e:
            logger.error(f"Failed to upload document to Supabase storage for app {application_id}: {e}")
            raise HTTPException(status_code=500, detail="Failed to upload document to storage provider. Please retry.")
    else:
        # Local storage (100% backward compatible)
        os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
        filename = f"{uuid.uuid4().hex}{ext}"
        file_path = os.path.join(settings.UPLOAD_DIR, filename)
        with open(file_path, "wb") as f:
            f.write(contents)

    # Validate document_type enum
    try:
        doc_type = DocumentType(document_type)
    except ValueError:
        try:
            if is_supabase:
                storage.delete_file(settings.SUPABASE_DOCUMENTS_BUCKET, file_path)
            elif os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=f"Invalid document_type: {document_type}")

    doc = Document(
        application_id=application_id,
        document_type=doc_type,
        joint_applicant_index=joint_applicant_index,
        file_path=file_path,
        original_name=file.filename,
    )
    db.add(doc)
    try:
        db.commit()
        db.refresh(doc)
    except Exception as commit_err:
        db.rollback()
        cleanup_success = True
        try:
            if is_supabase:
                cleanup_success = storage.delete_file(settings.SUPABASE_DOCUMENTS_BUCKET, file_path)
            elif os.path.exists(file_path):
                os.remove(file_path)
        except Exception as cleanup_err:
            cleanup_success = False
            logger.error(
                f"[StorageCompensationFailed] Failed compensating delete for key '{file_path}' "
                f"in bucket '{settings.SUPABASE_DOCUMENTS_BUCKET}' after DB commit error: {cleanup_err}"
            )

        if not cleanup_success and is_supabase:
            logger.error(
                f"[OrphanedStorageObject] Unreferenced object '{file_path}' in bucket "
                f"'{settings.SUPABASE_DOCUMENTS_BUCKET}' requires administrative cleanup."
            )

        logger.error(f"Database commit error while saving document for app {application_id}: {commit_err}")
        raise HTTPException(status_code=500, detail="Database commit failed while saving document. Please retry.")

    logger.info(f"Document {doc.id} uploaded for application {application_id}")
    return doc


@router.post("/process/{document_id}", response_model=DocumentOut)
def process_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Preprocess image + run OCR on a document."""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    app = db.query(Application).filter(Application.id == doc.application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    authorize_application_access(app, current_user)

    # Storage integration: secure transient local file for native OCR and vision libraries
    storage = get_storage_service()

    try:
        temp_file_ctx = storage.get_local_temp_file(settings.SUPABASE_DOCUMENTS_BUCKET, doc.file_path)
    except Exception as e:
        logger.error(f"Failed to access document storage for doc {doc.id} ({doc.file_path}): {e}")
        raise HTTPException(status_code=500, detail="Failed to access document from storage provider. Please retry.")

    with temp_file_ctx as local_doc_path:
        # Preprocess (skip for PDFs handled by pdf2image inside ocr_service)
        preprocessed_path = local_doc_path
        if not local_doc_path.endswith(".pdf"):
            try:
                preprocessed_path = preprocess_image(local_doc_path)
            except Exception as e:
                logger.warning(f"Preprocessing failed: {e}")
                preprocessed_path = local_doc_path

        doc.processed = 1
        try:
            db.commit()
        except (StaleDataError, ObjectDeletedError):
            db.rollback()
            if preprocessed_path != local_doc_path and os.path.exists(preprocessed_path):
                try:
                    os.remove(preprocessed_path)
                except OSError:
                    pass
            raise HTTPException(
                status_code=409,
                detail="Document was removed while processing. Please upload it again."
            )
        except Exception as e:
            db.rollback()
            if preprocessed_path != local_doc_path and os.path.exists(preprocessed_path):
                try:
                    os.remove(preprocessed_path)
                except OSError:
                    pass
            logger.error(f"Failed to set document {document_id} status to processing: {e}")
            raise HTTPException(status_code=500, detail="Failed to initialize document processing")

        # Local references for OCR processing
        doc_type_val = doc.document_type.value if hasattr(doc.document_type, "value") else str(doc.document_type)

        multipass_failed = False
        structured_data = None
        extracted_text = ""

        # OCR and NLP Extraction via Multi-Pass Pipeline
        try:
            # 1. Run multi-pass OCR workflow with image quality analysis
            multipass_result = run_multipass_ocr(preprocessed_path, doc_type=doc_type_val)
            extracted_text = multipass_result.get("primary_text", "")

            # 2. Multi-pass consensus and field-level confidence extraction
            structured_data = extract_information(multipass_result, doc_type=doc_type_val)

            # 3. Aadhaar address quality gate – Gemini Vision fallback
            #    Triggered only when the OCR address is missing or garbled.
            #    Does NOT affect any other fields.
            if doc_type_val == "aadhaar":
                ocr_address = structured_data.get("address")
                if should_use_vision_fallback(ocr_address, local_doc_path):
                    logger.info(
                        f"Document {document_id}: OCR address quality insufficient – "
                        f"invoking Gemini Vision address fallback"
                    )
                    vision_address = extract_aadhaar_address_via_gemini(
                        local_doc_path,
                        applicant_name=structured_data.get("applicant_name") or structured_data.get("name"),
                        aadhaar_number=structured_data.get("aadhaar_number"),
                    )
                    if vision_address:
                        structured_data["address"] = vision_address
                        if "fields" not in structured_data:
                            structured_data["fields"] = {}
                        structured_data["fields"]["address"] = {
                            "value": vision_address,
                            "confidence": 0.88,
                            "confidence_level": "high",
                            "source_document": "aadhaar",
                            "extraction_method": "gemini_vision_fallback",
                            "validation_status": "valid",
                            "raw_match": vision_address,
                            "warning": None,
                        }
                        logger.info(
                            f"Document {document_id}: Gemini Vision address set "
                            f"({len(vision_address)} chars)"
                        )
                    else:
                        logger.warning(
                            f"Document {document_id}: Gemini Vision address fallback "
                            f"returned nothing – retaining OCR result with warning"
                        )
                        if "fields" not in structured_data:
                            structured_data["fields"] = {}
                        if "address" not in structured_data["fields"]:
                            structured_data["fields"]["address"] = {"value": ocr_address or ""}
                        structured_data["fields"]["address"]["warning"] = (
                            "⚠ Address could not be confidently extracted — enter manually"
                        )
                        structured_data["fields"]["address"]["confidence_level"] = "low"
                        structured_data["fields"]["address"]["extraction_method"] = "ocr_low_confidence"

                # Preserve all original fields and ensure canonical & alias compatibility:
                # 1. Name: ensure both 'applicant_name' and 'name' are present and synchronized
                name_val = structured_data.get("applicant_name") or structured_data.get("name")
                if name_val:
                    structured_data["applicant_name"] = name_val
                    structured_data["name"] = name_val
                    if "fields" in structured_data:
                        name_meta = structured_data["fields"].get("applicant_name") or structured_data["fields"].get("name")
                        if name_meta:
                            structured_data["fields"]["applicant_name"] = name_meta
                            structured_data["fields"]["name"] = name_meta

                # 2. Aadhaar Number: preserve and ensure all alias keys are populated
                aadhaar_val = (
                    structured_data.get("aadhaar_number")
                    or structured_data.get("aadhaar")
                    or structured_data.get("aadhaar_no")
                    or structured_data.get("uid")
                )
                if aadhaar_val:
                    structured_data["aadhaar_number"] = aadhaar_val
                    structured_data["aadhaar"] = aadhaar_val
                    structured_data["aadhaar_no"] = aadhaar_val
                    structured_data["uid"] = aadhaar_val
                    if "fields" in structured_data:
                        aadhaar_meta = (
                            structured_data["fields"].get("aadhaar_number")
                            or structured_data["fields"].get("aadhaar")
                            or structured_data["fields"].get("aadhaar_no")
                            or structured_data["fields"].get("uid")
                        )
                        if aadhaar_meta:
                            for k_alias in ["aadhaar_number", "aadhaar", "aadhaar_no", "uid"]:
                                structured_data["fields"][k_alias] = aadhaar_meta

                # 3. Father's Name: ensure both 'father_name' and 'father\'s_name' are populated
                father_val = structured_data.get("father_name") or structured_data.get("father's_name")
                if father_val:
                    structured_data["father_name"] = father_val
                    structured_data["father's_name"] = father_val
                    if "fields" in structured_data:
                        father_meta = structured_data["fields"].get("father_name") or structured_data["fields"].get("father's_name")
                        if father_meta:
                            structured_data["fields"]["father_name"] = father_meta
                            structured_data["fields"]["father's_name"] = father_meta

        except Exception as e:
            logger.warning(f"Multi-pass OCR pipeline exception for {document_id}: {e}. Falling back to standard OCR.")
            multipass_failed = True

        if multipass_failed:
            try:
                text = extract_text(preprocessed_path)
                extracted_text = text
                structured_data = extract_information(text, doc_type=doc_type_val)
            except Exception as e2:
                logger.error(f"Fallback OCR also failed for {document_id}: {e2}")
                try:
                    doc_still_exists = db.query(Document).filter(Document.id == document_id).first()
                    if not doc_still_exists:
                        db.rollback()
                        logger.warning(f"Document {document_id} was removed while processing.")
                        raise HTTPException(
                            status_code=409,
                            detail="Document was removed while processing. Please upload it again."
                        )
                    doc_still_exists.processed = 3
                    if not doc_still_exists.structured_data:
                        doc_still_exists.structured_data = json.dumps({"status": "processing_failed", "error": str(e2)})
                    db.commit()
                except HTTPException:
                    raise
                except (StaleDataError, ObjectDeletedError):
                    db.rollback()
                    logger.warning(f"Document {document_id} was removed while persisting failure state.")
                    raise HTTPException(
                        status_code=409,
                        detail="Document was removed while processing. Please upload it again."
                    )
                except Exception as dberr:
                    db.rollback()
                    logger.error(f"Database error while recording failure for {document_id}: {dberr}")
                raise HTTPException(status_code=500, detail="Document processing failed")
            finally:
                if preprocessed_path != local_doc_path and os.path.exists(preprocessed_path):
                    try:
                        os.remove(preprocessed_path)
                    except OSError:
                        pass
        else:
            if preprocessed_path != local_doc_path and os.path.exists(preprocessed_path):
                try:
                    os.remove(preprocessed_path)
                except OSError:
                    pass

    # Persist the extraction result safely
    try:
        doc_to_update = db.query(Document).filter(Document.id == document_id).first()
        if not doc_to_update:
            db.rollback()
            logger.warning(f"Document {document_id} was removed while processing.")
            raise HTTPException(
                status_code=409,
                detail="Document was removed while processing. Please upload it again."
            )

        doc_to_update.extracted_text = extracted_text
        doc_to_update.structured_data = json.dumps(structured_data) if structured_data is not None else None
        doc_to_update.processed = 2
        db.commit()
        db.refresh(doc_to_update)
        logger.info(f"Multi-pass OCR complete for document {document_id}: {len(doc_to_update.extracted_text or '')} chars")
        return doc_to_update
    except HTTPException:
        raise
    except (StaleDataError, ObjectDeletedError) as stale_err:
        db.rollback()
        logger.warning(f"Document {document_id} was deleted concurrently during processing: {stale_err}")
        raise HTTPException(
            status_code=409,
            detail="Document was removed while processing. Please upload it again."
        )
    except Exception as commit_err:
        db.rollback()
        logger.error(f"Unexpected database error saving document {document_id}: {commit_err}")
        raise HTTPException(status_code=500, detail="Document processing could not be saved")



@router.get("/{document_id}/file")
@router.get("/{document_id}/download")
def get_document_file(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Securely stream/download an applicant document after verifying authorization."""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    app = db.query(Application).filter(Application.id == doc.application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    authorize_application_access(app, current_user)

    if not doc.file_path:
        raise HTTPException(status_code=404, detail="File path not recorded for this document")

    storage = get_storage_service()
    if settings.STORAGE_PROVIDER == "supabase":
        try:
            data = storage.download_file(settings.SUPABASE_DOCUMENTS_BUCKET, doc.file_path)
            ext = os.path.splitext(doc.file_path)[1].lower()
            media_type = "application/pdf" if ext == ".pdf" else f"image/{ext.lstrip('.') or 'png'}"
            return Response(content=data, media_type=media_type)
        except Exception as e:
            logger.error(f"Failed to download document from storage provider: {e}")
            raise HTTPException(status_code=500, detail="Failed to retrieve document from storage provider")

    # Local storage
    file_path = doc.file_path
    if not os.path.exists(file_path):
        candidate = os.path.join(settings.UPLOAD_DIR, os.path.basename(file_path))
        if os.path.exists(candidate):
            file_path = candidate
        else:
            raise HTTPException(status_code=404, detail="Document file not found on disk")

    ext = os.path.splitext(file_path)[1].lower()
    media_type = "application/pdf" if ext == ".pdf" else f"image/{ext.lstrip('.') or 'png'}"
    return FileResponse(file_path, media_type=media_type, filename=doc.original_name or os.path.basename(file_path))


@router.get("/{application_id}", response_model=List[DocumentOut])
def list_documents(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = db.query(Application).filter(Application.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    authorize_application_access(app, current_user)
    return db.query(Document).filter(Document.application_id == application_id).all()


@router.delete("/by-type/{application_id}/{doc_type}", status_code=200)
def delete_documents_by_type(
    application_id: int,
    doc_type: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete all documents of a given type for an application, clean up stored files, and reset gov verification status."""
    app = db.query(Application).filter(Application.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    authorize_application_access(app, current_user)

    docs = db.query(Document).filter(
        Document.application_id == application_id,
        Document.document_type == doc_type
    ).all()

    storage = get_storage_service()
    deleted_count = 0
    storage_failures = []

    for doc in docs:
        if not doc.file_path:
            db.delete(doc)
            db.commit()
            deleted_count += 1
            continue

        if settings.STORAGE_PROVIDER == "supabase":
            try:
                success = storage.delete_file(settings.SUPABASE_DOCUMENTS_BUCKET, doc.file_path)
                if not success:
                    raise StorageError(f"Failed to delete {doc.file_path} from storage bucket")
            except Exception as e:
                logger.error(f"Failed to delete stored object {doc.file_path} from Supabase: {e}")
                storage_failures.append(doc.id)
                break
        else:
            if os.path.exists(doc.file_path):
                try:
                    os.remove(doc.file_path)
                except Exception as e:
                    logger.warning(f"Could not remove file {doc.file_path}: {e}")

        base, _ = os.path.splitext(doc.file_path)
        prep_file = f"{base}_prep.png"
        if os.path.exists(prep_file):
            try:
                os.remove(prep_file)
            except Exception:
                pass

        # Storage deletion succeeded: delete DB record and commit synchronously
        # Guarantees no committed DB record points to a missing storage file
        db.delete(doc)
        db.commit()
        deleted_count += 1

    if storage_failures:
        remaining_count = len(docs) - deleted_count
        raise HTTPException(
            status_code=502,
            detail=(
                f"Storage deletion failed for document {storage_failures[0]}. "
                f"{deleted_count} document(s) successfully deleted; {remaining_count} document(s) preserved."
            )
        )

    # Clean up gov verification status if all documents of this type are deleted
    remaining_docs = db.query(Document).filter(
        Document.application_id == application_id,
        Document.document_type == doc_type
    ).count()

    if remaining_docs == 0:
        gv = db.query(GovVerification).filter(GovVerification.application_id == application_id).first()
        if gv:
            if doc_type == "aadhaar":
                gv.aadhaar_validity_status = "Pending"
                gv.aadhaar_screenshot_path = None
            elif doc_type == "pan":
                gv.pan_aadhaar_link_status = "Pending"
                gv.screenshot_path = None

        if doc_type == "aadhaar":
            app.aadhaar_number = None
        elif doc_type == "pan":
            app.pan_number = None

        db.commit()

    logger.info(f"Deleted {deleted_count} documents of type {doc_type} for application {application_id}")
    return {"status": "deleted", "application_id": application_id, "document_type": doc_type, "deleted_count": deleted_count}


@router.delete("/{document_id}", status_code=200)
def delete_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a document by id, clean up stored files and preprocessed variants, and reset associated gov verification status."""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    app = db.query(Application).filter(Application.id == doc.application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    authorize_application_access(app, current_user)

    app_id = doc.application_id
    doc_type_val = doc.document_type.value if hasattr(doc.document_type, "value") else str(doc.document_type)

    storage = get_storage_service()

    if doc.file_path:
        if settings.STORAGE_PROVIDER == "supabase":
            try:
                success = storage.delete_file(settings.SUPABASE_DOCUMENTS_BUCKET, doc.file_path)
                if not success:
                    raise StorageError(f"Failed to delete {doc.file_path} from storage bucket")
            except Exception as e:
                logger.error(f"Failed to delete stored object {doc.file_path} from Supabase: {e}")
                raise HTTPException(
                    status_code=502,
                    detail=f"Storage deletion failed for document {doc.id}. Document preserved.",
                )
        else:
            if os.path.exists(doc.file_path):
                try:
                    os.remove(doc.file_path)
                except Exception as e:
                    logger.warning(f"Could not remove file {doc.file_path}: {e}")

        base, _ = os.path.splitext(doc.file_path)
        prep_file = f"{base}_prep.png"
        if os.path.exists(prep_file):
            try:
                os.remove(prep_file)
            except Exception:
                pass

    db.delete(doc)
    db.commit()

    # Reset gov_verifications audit status and screenshot paths only when no documents of that type remain
    remaining_count = db.query(Document).filter(
        Document.application_id == app_id,
        Document.document_type == doc.document_type,
    ).count()

    if remaining_count == 0:
        gv = db.query(GovVerification).filter(GovVerification.application_id == app_id).first()
        if gv:
            if doc_type_val == "aadhaar":
                gv.aadhaar_validity_status = "Pending"
                gv.aadhaar_screenshot_path = None
            elif doc_type_val == "pan":
                gv.pan_aadhaar_link_status = "Pending"
                gv.screenshot_path = None

        if doc_type_val == "aadhaar":
            app.aadhaar_number = None
        elif doc_type_val == "pan":
            app.pan_number = None

        db.commit()

    logger.info(f"Document {document_id} of type {doc_type_val} for app {app_id} deleted successfully")
    return {"status": "deleted", "id": document_id, "application_id": app_id, "document_type": doc_type_val}


@router.get("/validate-required/{application_id}")
def check_required_documents(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Validate that mandatory documents (Aadhaar Card and PAN Card) are uploaded for an application.
    All other document types are optional.
    """
    app = db.query(Application).filter(Application.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    authorize_application_access(app, current_user)

    docs = db.query(Document).filter(Document.application_id == application_id).all()
    result = validate_required_documents(docs)
    return {
        "application_id": application_id,
        **result,
    }

