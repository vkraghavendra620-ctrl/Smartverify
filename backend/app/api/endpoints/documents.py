"""Document upload and processing endpoints."""
import os, uuid, logging, json
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from typing import List

from app.db.database import get_db
from app.models.document import Document, DocumentType
from app.models.application import Application
from app.models.user import User
from app.schemas.document import DocumentOut
from app.core.security import get_current_user
from app.core.config import settings
from app.services.preprocessing import preprocess_image
from app.services.ocr_service import extract_text
from app.services.nlp_service import extract_information
from app.services.classification_service import classify_document

router = APIRouter()
logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
MAX_BYTES = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024


from typing import List, Optional

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
    # Validate application ownership
    app = db.query(Application).filter(Application.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    if current_user.role != "admin" and app.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Validate file extension
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"File type {ext} not allowed")

    # Read and size-check
    contents = await file.read()
    if len(contents) > MAX_BYTES:
        raise HTTPException(status_code=400, detail="File too large")

    # Persist file
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(settings.UPLOAD_DIR, filename)
    with open(file_path, "wb") as f:
        f.write(contents)

    # Validate document_type enum
    try:
        doc_type = DocumentType(document_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid document_type: {document_type}")

    doc = Document(
        application_id=application_id,
        document_type=doc_type,
        joint_applicant_index=joint_applicant_index,
        file_path=file_path,
        original_name=file.filename,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    logger.info(f"Document {doc.id} uploaded for application {application_id}")
    return doc


@router.post("/process/{document_id}", response_model=DocumentOut)
async def process_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Preprocess image + run OCR on a document."""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Preprocess (skip for PDFs handled by pdf2image inside ocr_service)
    if not doc.file_path.endswith(".pdf"):
        try:
            preprocessed_path = preprocess_image(doc.file_path)
        except Exception as e:
            logger.warning(f"Preprocessing failed: {e}")
            preprocessed_path = doc.file_path
    else:
        preprocessed_path = doc.file_path

    doc.processed = 1
    db.commit()

    # OCR and NLP Extraction
    try:
        text = extract_text(preprocessed_path)
        doc.extracted_text = text
        
        # Run NLP extraction on the OCR text
        structured_data = extract_information(text, doc_type=doc.document_type.value)
        doc.structured_data = json.dumps(structured_data)
        
        doc.processed = 2
        db.commit()
        logger.info(f"OCR and NLP complete for document {document_id}: {len(text)} chars")
    except Exception as e:
        logger.error(f"OCR/NLP failed for {document_id}: {e}")
        raise HTTPException(status_code=500, detail="Document processing failed")

    db.refresh(doc)
    return doc


@router.get("/{application_id}", response_model=List[DocumentOut])
def list_documents(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.query(Document).filter(Document.application_id == application_id).all()
