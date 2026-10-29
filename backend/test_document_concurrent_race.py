"""Test concurrent document deletion race condition during OCR processing."""
import io
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.main import app
from app.core.security import create_access_token
from app.db.database import SessionLocal
from app.models.user import User
from app.models.document import Document
from app.models.application import Application


def test_concurrent_deletion_during_ocr_processing():
    """Simulate Document deleted while OCR is in-flight:
    1. Upload document to an isolated test application.
    2. OCR runs, but before persisting results, document is deleted by another session.
    3. Backend returns clean 409 with 'Document was removed while processing. Please upload it again.'
    4. No PendingRollbackError or 500 error.
    5. Session remains healthy.
    """
    db = SessionLocal()
    client = TestClient(app)
    test_app = None
    try:
        admin = db.query(User).filter(User.role == "admin").first() or db.query(User).first()
        assert admin is not None
        token = create_access_token({"sub": str(admin.id), "role": admin.role})
        headers = {"Authorization": f"Bearer {token}"}

        # Create isolated test application
        test_app = Application(
            user_id=admin.id,
            applicant_name="Race Test Applicant",
            loan_amount=500000.0,
            loan_type="Personal Loan"
        )
        db.add(test_app)
        db.commit()
        db.refresh(test_app)
        app_id = test_app.id

        # 1. Upload Aadhaar document
        fake_aadhaar = b"fake-aadhaar-bytes-for-test"
        up_res = client.post(
            "/documents/upload",
            headers=headers,
            data={"application_id": str(app_id), "document_type": "aadhaar"},
            files={"file": ("test_race_aadhaar.png", io.BytesIO(fake_aadhaar), "image/png")}
        )
        assert up_res.status_code in (200, 201)
        doc_id = up_res.json()["id"]

        # Hook into extract_information: when called during process_document, delete the doc row from DB!
        from app.services import nlp_service
        orig_extract_info = nlp_service.extract_information

        def delete_during_ocr(*args, **kwargs):
            # Simulate concurrent DELETE request from frontend
            del_res = client.delete(f"/documents/{doc_id}", headers=headers)
            assert del_res.status_code == 200
            return orig_extract_info(*args, **kwargs)

        with patch("app.api.endpoints.documents.extract_information", side_effect=delete_during_ocr):
            proc_res = client.post(f"/documents/process/{doc_id}", headers=headers)

        # Confirm controlled 409 response
        assert proc_res.status_code == 409, f"Expected 409, got {proc_res.status_code}: {proc_res.text}"
        data = proc_res.json()
        assert "Document was removed while processing" in data.get("detail", "")

        # Verify the document was NOT silently recreated
        re_check = db.query(Document).filter(Document.id == doc_id).first()
        assert re_check is None

        # Verify database session is fully functional (no PendingRollbackError)
        healthy_query = db.query(Application).filter(Application.id == app_id).first()
        assert healthy_query is not None

    finally:
        if test_app and test_app.id:
            try:
                db.query(Document).filter(Document.application_id == test_app.id).delete()
                db.query(Application).filter(Application.id == test_app.id).delete()
                db.commit()
            except Exception:
                db.rollback()
        db.close()


def test_normal_upload_process_remove_replace_flow():
    """Verify standard lifecycle:
    1. Upload document to an isolated test application.
    2. Process normally.
    3. Remove after processing completes.
    4. Upload replacement.
    5. Process replacement.
    """
    db = SessionLocal()
    client = TestClient(app)
    test_app = None
    try:
        admin = db.query(User).filter(User.role == "admin").first() or db.query(User).first()
        assert admin is not None
        token = create_access_token({"sub": str(admin.id), "role": admin.role})
        headers = {"Authorization": f"Bearer {token}"}

        # Create isolated test application
        test_app = Application(
            user_id=admin.id,
            applicant_name="Replace Test Applicant",
            loan_amount=300000.0,
            loan_type="Personal Loan"
        )
        db.add(test_app)
        db.commit()
        db.refresh(test_app)
        app_id = test_app.id

        # 1. Upload initial document
        fake_doc = b"fake-pan-initial-bytes"
        up_res1 = client.post(
            "/documents/upload",
            headers=headers,
            data={"application_id": str(app_id), "document_type": "pan"},
            files={"file": ("initial_pan.png", io.BytesIO(fake_doc), "image/png")}
        )
        assert up_res1.status_code in (200, 201)
        doc_id_1 = up_res1.json()["id"]

        # 2. Process initial document
        proc_res1 = client.post(f"/documents/process/{doc_id_1}", headers=headers)
        assert proc_res1.status_code == 200
        assert proc_res1.json()["processed"] == 2

        # 3. Remove after processing completes
        del_res = client.delete(f"/documents/{doc_id_1}", headers=headers)
        assert del_res.status_code == 200

        # Confirm deleted
        check_del = db.query(Document).filter(Document.id == doc_id_1).first()
        assert check_del is None

        # 4. Upload replacement
        fake_doc_replacement = b"fake-pan-replacement-bytes"
        up_res2 = client.post(
            "/documents/upload",
            headers=headers,
            data={"application_id": str(app_id), "document_type": "pan"},
            files={"file": ("replacement_pan.png", io.BytesIO(fake_doc_replacement), "image/png")}
        )
        assert up_res2.status_code in (200, 201)
        doc_id_2 = up_res2.json()["id"]
        assert doc_id_2 != doc_id_1

        # 5. Process replacement
        proc_res2 = client.post(f"/documents/process/{doc_id_2}", headers=headers)
        assert proc_res2.status_code == 200
        assert proc_res2.json()["processed"] == 2

    finally:
        if test_app and test_app.id:
            try:
                db.query(Document).filter(Document.application_id == test_app.id).delete()
                db.query(Application).filter(Application.id == test_app.id).delete()
                db.commit()
            except Exception:
                db.rollback()
        db.close()
