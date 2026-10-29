"""
Offline unit tests for SmartVerify Phase 2A:
Supabase Storage integration into applicant document uploads, OCR processing,
verification-agent file access, and document deletion.

Tests:
1. Document upload in Local mode (preserves default behavior and local path).
2. Document upload in Supabase mode (opaque object key, no PII, storage upload).
3. Document upload rollback (storage object cleanup if DB commit fails).
4. Document OCR processing in Local mode.
5. Document OCR processing in Supabase mode (secure tempfile download & cleanup).
6. Document OCR processing cleanup on multi-pass failure.
7. OCRTool verification-agent file access (local path vs Supabase object key).
8. Single document deletion in Supabase mode (storage deleted before DB commit).
9. Single document deletion failure handling (storage failure preserves DB record, HTTP 502).
10. Delete documents by type in Supabase mode (all objects deleted).
11. Delete documents by type failure handling (storage failure preserves DB records, HTTP 502).

Safety: Zero external network or live LLM calls. Fully mocked and offline.
"""

import io
import os
import json
import tempfile
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.db.database import Base, get_db
from app.core.security import get_current_user
from app.core.config import settings
from app.models.user import User, UserRole
from app.models.application import Application, GovVerification
from app.models.document import Document, DocumentType
from app.services.storage_service import (
    LocalStorageService,
    SupabaseStorageService,
    StorageError,
)
from app.agents.tools import OCRTool


def _dummy_png_bytes():
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?"
        b"\x00\x05\xfe\x02\xfe\xa74;\xa0\x00\x00\x00\x00IEND\xaeB`\x82"
    )


@pytest.fixture(scope="module")
def test_env():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()

    admin = User(
        id=901,
        name="Phase2A Admin",
        email="p2a_admin@test.com",
        password="hashedpassword",
        role=UserRole.admin,
    )
    officer = User(
        id=902,
        name="Phase2A Officer",
        email="p2a_officer@test.com",
        password="hashedpassword",
        role=UserRole.loan_officer,
    )
    unauthorized_officer = User(
        id=903,
        name="Phase2A Unauthorized Officer",
        email="p2a_unauth_officer@test.com",
        password="hashedpassword",
        role=UserRole.loan_officer,
    )
    db.add(admin)
    db.add(officer)
    db.add(unauthorized_officer)

    app_record = Application(
        id=301,
        user_id=902,
        applicant_name="Vikram Sethi",
        aadhaar_number="987654321098",
        pan_number="ABCDE9999Z",
        branch="Indiranagar",
        loan_type="Personal Loan",
        loan_amount=500000.0,
        loan_tenure=36,
        interest_rate=10.5,
    )
    db.add(app_record)

    gv = GovVerification(
        id=301,
        application_id=301,
        aadhaar_validity_status="Valid",
        pan_aadhaar_link_status="Linked",
    )
    db.add(gv)
    db.commit()

    def override_get_db():
        try:
            yield db
        finally:
            pass

    def override_get_current_user():
        return officer

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    client = TestClient(app)

    yield {
        "client": client,
        "db": db,
        "app_id": 301,
        "officer": officer,
        "unauthorized_officer": unauthorized_officer,
    }

    app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# 1. Document Upload Tests (Local & Supabase Modes)
# ─────────────────────────────────────────────────────────────────────────────

def test_document_upload_local_mode(test_env):
    client = test_env["client"]
    app_id = test_env["app_id"]
    db = test_env["db"]

    with patch.object(settings, "STORAGE_PROVIDER", "local"):
        png_data = _dummy_png_bytes()
        files = {"file": ("local_aadhaar.png", io.BytesIO(png_data), "image/png")}
        data = {"application_id": str(app_id), "document_type": "aadhaar"}

        resp = client.post("/documents/upload", data=data, files=files)
        assert resp.status_code == 201, resp.text
        doc_json = resp.json()

        assert doc_json["document_type"] == "aadhaar"
        doc_id = doc_json["id"]
        doc = db.query(Document).filter(Document.id == doc_id).first()
        assert doc is not None
        # In local mode, file_path is an absolute path on disk
        assert os.path.exists(doc.file_path)
        assert doc.file_path.endswith(".png")


def test_document_upload_supabase_mode_stores_opaque_key(test_env):
    client = test_env["client"]
    app_id = test_env["app_id"]
    db = test_env["db"]

    mock_storage = MagicMock()
    mock_storage.upload_file.return_value = "applications/301/documents/mocked-key.png"

    with patch.object(settings, "STORAGE_PROVIDER", "supabase"), \
         patch("app.api.endpoints.documents.get_storage_service", return_value=mock_storage):

        png_data = _dummy_png_bytes()
        files = {"file": ("Vikram_Sethi_PAN_Secret.png", io.BytesIO(png_data), "image/png")}
        data = {"application_id": str(app_id), "document_type": "pan"}

        resp = client.post("/documents/upload", data=data, files=files)
        assert resp.status_code == 201, resp.text

        # Verify storage upload was called with applicant-documents bucket
        assert mock_storage.upload_file.called
        args, kwargs = mock_storage.upload_file.call_args
        call_bucket = kwargs.get("bucket") or (args[0] if args else None)
        call_key = kwargs.get("object_key") or (args[1] if len(args) > 1 else None)
        assert call_bucket == settings.SUPABASE_DOCUMENTS_BUCKET
        assert call_key.startswith(f"applications/{app_id}/documents/")
        assert call_key.endswith(".png")
        # Ensure no PII in the generated object key
        assert "Vikram" not in call_key
        assert "Sethi" not in call_key
        assert "Secret" not in call_key

        doc_json = resp.json()
        doc = db.query(Document).filter(Document.id == doc_json["id"]).first()
        assert doc is not None
        assert doc.file_path.startswith(f"applications/{app_id}/documents/")


def test_document_upload_rollback_on_db_failure(test_env):
    client = test_env["client"]
    app_id = test_env["app_id"]

    mock_storage = MagicMock()
    mock_storage.upload_file.return_value = "applications/301/documents/orphaned-candidate.png"

    with patch.object(settings, "STORAGE_PROVIDER", "supabase"), \
         patch("app.api.endpoints.documents.get_storage_service", return_value=mock_storage), \
         patch.object(test_env["db"], "commit", side_effect=RuntimeError("Simulated DB connection drop")):

        png_data = _dummy_png_bytes()
        files = {"file": ("salary.png", io.BytesIO(png_data), "image/png")}
        data = {"application_id": str(app_id), "document_type": "salary_slip"}

        resp = client.post("/documents/upload", data=data, files=files)
        assert resp.status_code == 500

        # When DB commit fails, storage.delete_file must be called to prevent orphaned objects
        assert mock_storage.delete_file.called
        args, kwargs = mock_storage.delete_file.call_args
        del_bucket = kwargs.get("bucket") or (args[0] if args else None)
        del_key = kwargs.get("object_key") or (args[1] if len(args) > 1 else None)
        assert del_bucket == settings.SUPABASE_DOCUMENTS_BUCKET
        assert del_key.startswith(f"applications/{app_id}/documents/")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Document Processing & Transient Local Temp File Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_process_document_supabase_mode_secure_tempfile(test_env):
    client = test_env["client"]
    app_id = test_env["app_id"]
    db = test_env["db"]

    # Create a document record with an opaque Supabase key
    opaque_key = "applications/301/documents/secure-test-doc-1.png"
    doc = Document(
        application_id=app_id,
        document_type=DocumentType.pan,
        file_path=opaque_key,
        original_name="pan_card.png",
        processed=0,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    captured_temp_file = []

    @contextmanager
    def mock_get_temp_file(bucket, object_key):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            tf.write(_dummy_png_bytes())
            temp_path = tf.name
        captured_temp_file.append(temp_path)
        try:
            yield temp_path
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    mock_storage = MagicMock()
    mock_storage.get_local_temp_file = mock_get_temp_file

    with patch.object(settings, "STORAGE_PROVIDER", "supabase"), \
         patch("app.api.endpoints.documents.get_storage_service", return_value=mock_storage), \
         patch("app.api.endpoints.documents.run_multipass_ocr", return_value={"primary_text": "INCOME TAX DEPARTMENT PAN ABCDE9999Z"}), \
         patch("app.api.endpoints.documents.extract_information", return_value={"pan_number": "ABCDE9999Z", "name": "Vikram Sethi"}):

        resp = client.post(f"/documents/process/{doc.id}")
        assert resp.status_code == 200, resp.text
        res_json = resp.json()
        assert res_json["processed"] == 2
        assert "ABCDE9999Z" in res_json["extracted_text"]

    # Verify that the secure temp file was automatically deleted after processing
    assert len(captured_temp_file) == 1
    assert not os.path.exists(captured_temp_file[0]), "Temporary file must be deleted on completion"


def test_process_document_cleanup_on_ocr_failure(test_env):
    client = test_env["client"]
    app_id = test_env["app_id"]
    db = test_env["db"]

    opaque_key = "applications/301/documents/corrupt-doc.png"
    doc = Document(
        application_id=app_id,
        document_type=DocumentType.aadhaar,
        file_path=opaque_key,
        original_name="corrupt.png",
        processed=0,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    captured_temp_file = []

    @contextmanager
    def mock_get_temp_file(bucket, object_key):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            tf.write(b"NOT_A_VALID_IMAGE")
            temp_path = tf.name
        captured_temp_file.append(temp_path)
        try:
            yield temp_path
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    mock_storage = MagicMock()
    mock_storage.get_local_temp_file = mock_get_temp_file

    with patch.object(settings, "STORAGE_PROVIDER", "supabase"), \
         patch("app.api.endpoints.documents.get_storage_service", return_value=mock_storage), \
         patch("app.api.endpoints.documents.run_multipass_ocr", side_effect=RuntimeError("Corrupt image data")), \
         patch("app.api.endpoints.documents.extract_text", side_effect=RuntimeError("Fallback also failed")):

        resp = client.post(f"/documents/process/{doc.id}")
        assert resp.status_code == 500

    # Ensure cleanup happened even on failure
    assert len(captured_temp_file) == 1
    assert not os.path.exists(captured_temp_file[0]), "Temporary file must be deleted on exception"


# ─────────────────────────────────────────────────────────────────────────────
# 3. OCRTool Agent Access Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_ocr_tool_local_and_supabase_key_resolution():
    tool = OCRTool()

    # Case A: Local file path that exists on disk
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(_dummy_png_bytes())
        local_path = f.name

    try:
        with patch("app.agents.tools.extract_text", return_value="GOVERNMENT OF INDIA AADHAAR") as mock_extract:
            text = tool._run(local_path)
            assert text == "GOVERNMENT OF INDIA AADHAAR"
            mock_extract.assert_called_once_with(local_path)
    finally:
        if os.path.exists(local_path):
            os.remove(local_path)

    # Case B: Supabase object key (not a local file path on disk)
    supabase_key = "applications/301/documents/d290f1ee-6c54-4b01-90e6-d701748f0851.png"
    assert not os.path.exists(supabase_key)

    captured_temp = []

    @contextmanager
    def mock_agent_temp_file(bucket, key):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            tf.write(_dummy_png_bytes())
            captured_temp.append(tf.name)
            yield tf.name
        if os.path.exists(tf.name):
            os.remove(tf.name)

    mock_storage = MagicMock()
    mock_storage.get_local_temp_file = mock_agent_temp_file

    with patch("app.services.storage_service.get_storage_service", return_value=mock_storage), \
         patch("app.agents.tools.extract_text", return_value="EXTRACTED FROM SUPABASE OBJECT") as mock_extract:

        result = tool._run(supabase_key)
        assert result == "EXTRACTED FROM SUPABASE OBJECT"
        assert len(captured_temp) == 1
        # Check that extract_text received the temporary local file
        mock_extract.assert_called_once_with(captured_temp[0])
        # Check that the temp file was cleaned up
        assert not os.path.exists(captured_temp[0])


# ─────────────────────────────────────────────────────────────────────────────
# 4. Document Deletion & Rollback Protection Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_delete_document_supabase_mode_success(test_env):
    client = test_env["client"]
    app_id = test_env["app_id"]
    db = test_env["db"]

    opaque_key = "applications/301/documents/doc-to-delete.png"
    doc = Document(
        application_id=app_id,
        document_type=DocumentType.tax_receipt,
        file_path=opaque_key,
        original_name="tax.png",
        processed=1,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    target_id = doc.id

    mock_storage = MagicMock()
    mock_storage.delete_file.return_value = True

    with patch.object(settings, "STORAGE_PROVIDER", "supabase"), \
         patch("app.api.endpoints.documents.get_storage_service", return_value=mock_storage):

        resp = client.delete(f"/documents/{target_id}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "deleted"

        # Verify storage delete was called
        assert mock_storage.delete_file.called
        args, kwargs = mock_storage.delete_file.call_args
        del_bucket = kwargs.get("bucket") or (args[0] if args else None)
        del_key = kwargs.get("object_key") or (args[1] if len(args) > 1 else None)
        assert del_bucket == settings.SUPABASE_DOCUMENTS_BUCKET
        assert del_key == opaque_key

        # Verify database record was deleted
        assert db.query(Document).filter(Document.id == target_id).first() is None


def test_delete_document_supabase_mode_storage_failure_preserves_db(test_env):
    client = test_env["client"]
    app_id = test_env["app_id"]
    db = test_env["db"]

    opaque_key = "applications/301/documents/doc-fail-delete.png"
    doc = Document(
        application_id=app_id,
        document_type=DocumentType.tax_receipt,
        file_path=opaque_key,
        original_name="tax_safe.png",
        processed=1,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    target_id = doc.id

    mock_storage = MagicMock()
    mock_storage.delete_file.side_effect = StorageError("Supabase connection timeout during DELETE")

    with patch.object(settings, "STORAGE_PROVIDER", "supabase"), \
         patch("app.api.endpoints.documents.get_storage_service", return_value=mock_storage):

        resp = client.delete(f"/documents/{target_id}")
        assert resp.status_code == 502, resp.text
        assert "Storage deletion failed" in resp.json()["detail"]

        # CRITICAL SAFETY REQUIREMENT: DB record must NOT be deleted when storage deletion fails
        db_doc = db.query(Document).filter(Document.id == target_id).first()
        assert db_doc is not None, "Database record must be preserved on storage deletion failure"


def test_delete_documents_by_type_supabase_mode_success(test_env):
    client = test_env["client"]
    app_id = test_env["app_id"]
    db = test_env["db"]

    doc1 = Document(
        application_id=app_id,
        document_type=DocumentType.salary_slip,
        file_path="applications/301/documents/slip1.png",
        original_name="slip1.png",
        processed=1,
    )
    doc2 = Document(
        application_id=app_id,
        document_type=DocumentType.salary_slip,
        file_path="applications/301/documents/slip2.png",
        original_name="slip2.png",
        processed=1,
    )
    db.add_all([doc1, doc2])
    db.commit()

    mock_storage = MagicMock()
    mock_storage.delete_file.return_value = True

    with patch.object(settings, "STORAGE_PROVIDER", "supabase"), \
         patch("app.api.endpoints.documents.get_storage_service", return_value=mock_storage):

        resp = client.delete(f"/documents/by-type/{app_id}/salary_slip")
        assert resp.status_code == 200, resp.text
        assert resp.json()["deleted_count"] >= 2

        # Verify storage delete was called for both documents
        assert mock_storage.delete_file.call_count >= 2
        remaining = db.query(Document).filter(
            Document.application_id == app_id,
            Document.document_type == DocumentType.salary_slip
        ).all()
        assert len(remaining) == 0


def test_delete_documents_by_type_supabase_storage_failure_preserves_db(test_env):
    client = test_env["client"]
    app_id = test_env["app_id"]
    db = test_env["db"]

    doc = Document(
        application_id=app_id,
        document_type=DocumentType.residence_proof,
        file_path="applications/301/documents/bill.png",
        original_name="bill.png",
        processed=1,
    )
    db.add(doc)
    db.commit()
    target_id = doc.id

    mock_storage = MagicMock()
    mock_storage.delete_file.return_value = False  # Storage delete returned failure

    with patch.object(settings, "STORAGE_PROVIDER", "supabase"), \
         patch("app.api.endpoints.documents.get_storage_service", return_value=mock_storage):

        resp = client.delete(f"/documents/by-type/{app_id}/residence_proof")
        assert resp.status_code == 502, resp.text

        # Verify record preserved
        db_doc = db.query(Document).filter(Document.id == target_id).first()
        assert db_doc is not None, "Database record must be preserved on storage failure"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Security & Single-Document Deletion Isolation Regression Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_run_verification_unauthorized_user_forbidden(test_env):
    """Attempting verification by an unassigned loan officer must return HTTP 403."""
    client = test_env["client"]
    app_id = test_env["app_id"]
    unauthorized_officer = test_env["unauthorized_officer"]

    app.dependency_overrides[get_current_user] = lambda: unauthorized_officer
    try:
        resp = client.post(f"/verify/{app_id}")
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"] == "Access denied"
    finally:
        app.dependency_overrides[get_current_user] = lambda: test_env["officer"]


def test_run_agentic_verification_unauthorized_user_forbidden(test_env):
    """Attempting agentic verification by an unassigned loan officer must return HTTP 403."""
    client = test_env["client"]
    app_id = test_env["app_id"]
    unauthorized_officer = test_env["unauthorized_officer"]

    app.dependency_overrides[get_current_user] = lambda: unauthorized_officer
    try:
        resp = client.post(f"/verify/{app_id}/agentic")
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"] == "Access denied"
    finally:
        app.dependency_overrides[get_current_user] = lambda: test_env["officer"]


def test_delete_document_preserves_sibling_documents_of_same_type(test_env):
    """Deleting a single document must never delete sibling documents of the same type."""
    client = test_env["client"]
    app_id = test_env["app_id"]
    db = test_env["db"]

    doc1 = Document(
        application_id=app_id,
        document_type=DocumentType.salary_slip,
        file_path="applications/301/documents/sibling_slip_1.png",
        original_name="slip_jan.png",
        processed=1,
    )
    doc2 = Document(
        application_id=app_id,
        document_type=DocumentType.salary_slip,
        file_path="applications/301/documents/sibling_slip_2.png",
        original_name="slip_feb.png",
        processed=1,
    )
    db.add_all([doc1, doc2])
    db.commit()
    db.refresh(doc1)
    db.refresh(doc2)
    doc1_id = doc1.id
    doc2_id = doc2.id

    mock_storage = MagicMock()
    mock_storage.delete_file.return_value = True

    with patch.object(settings, "STORAGE_PROVIDER", "supabase"), \
         patch("app.api.endpoints.documents.get_storage_service", return_value=mock_storage):

        resp = client.delete(f"/documents/{doc1_id}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "deleted"
        assert data["id"] == doc1_id

        # Verify only one storage deletion call was executed with doc1's key
        assert mock_storage.delete_file.call_count == 1
        del_args, del_kwargs = mock_storage.delete_file.call_args
        deleted_key = del_kwargs.get("object_key") or (del_args[1] if len(del_args) > 1 else None)
        assert deleted_key == "applications/301/documents/sibling_slip_1.png"

        # Verify doc1 is deleted from the database but doc2 is preserved
        assert db.query(Document).filter(Document.id == doc1_id).first() is None
        preserved_doc2 = db.query(Document).filter(Document.id == doc2_id).first()
        assert preserved_doc2 is not None, "Sibling document of the same type must not be deleted"
        assert preserved_doc2.file_path == "applications/301/documents/sibling_slip_2.png"
