"""Focused tests for SmartVerify Mandatory Document Validation.

Requirements:
1. Only Aadhaar Card and PAN Card must be mandatory.
2. All other document types remain optional.
3. Validation behavior:
   - Aadhaar and PAN both uploaded: required-document validation passes.
   - Aadhaar missing: validation fails with a clear message naming Aadhaar.
   - PAN missing: validation fails with a clear message naming PAN.
   - Both missing: validation fails and identifies both.
   - Other documents missing: validation still passes when Aadhaar and PAN are present.
4. Fraud detection and verification engine behavior:
   - Preserves absence of other document types from causing required-document failure.
"""
import pytest
from app.services.document_validation import validate_required_documents, MANDATORY_DOCUMENT_TYPES
from app.services.verification_engine import VerificationEngine
from app.services.fraud_detection import FraudDetector
from app.models.document import Document, DocumentType


class MockDocument:
    def __init__(self, doc_type, joint_applicant_index=None):
        self.document_type = doc_type
        self.joint_applicant_index = joint_applicant_index


# ─── 1. Aadhaar & PAN both uploaded: passes ─────────────────────────────────

def test_aadhaar_and_pan_both_uploaded_passes():
    """When both Aadhaar and PAN are present, validation must pass."""
    docs = [
        {"document_type": "aadhaar"},
        {"document_type": "pan"},
    ]
    result = validate_required_documents(docs)
    assert result["is_valid"] is True
    assert result["has_aadhaar"] is True
    assert result["has_pan"] is True
    assert len(result["missing_documents"]) == 0
    assert len(result["missing_labels"]) == 0
    assert "passed" in result["message"].lower() or "present" in result["message"].lower()


def test_aadhaar_and_pan_with_model_objects_passes():
    """Test with model / object instances."""
    docs = [
        MockDocument(DocumentType.aadhaar),
        MockDocument(DocumentType.pan),
    ]
    result = validate_required_documents(docs)
    assert result["is_valid"] is True
    assert result["has_aadhaar"] is True
    assert result["has_pan"] is True


def test_aadhaar_and_pan_with_strings_passes():
    """Test with raw string types."""
    result = validate_required_documents(["aadhaar", "pan"])
    assert result["is_valid"] is True


# ─── 2. Aadhaar missing: fails with clear message ───────────────────────────

def test_aadhaar_missing_fails_with_clear_message():
    """When Aadhaar is missing, validation fails with clear message naming Aadhaar."""
    docs = [
        {"document_type": "pan"},
        {"document_type": "salary_slip"},
        {"document_type": "bank_statement"},
    ]
    result = validate_required_documents(docs)
    assert result["is_valid"] is False
    assert result["has_aadhaar"] is False
    assert result["has_pan"] is True
    assert "aadhaar" in result["missing_documents"]
    assert "pan" not in result["missing_documents"]
    assert "Aadhaar Card" in result["missing_labels"]
    assert "Aadhaar" in result["message"]
    assert "PAN" not in result["message"].split("missing:")[1] if "missing:" in result["message"] else True


# ─── 3. PAN missing: fails with clear message ───────────────────────────────

def test_pan_missing_fails_with_clear_message():
    """When PAN is missing, validation fails with clear message naming PAN."""
    docs = [
        {"document_type": "aadhaar"},
        {"document_type": "tax_receipt"},
        {"document_type": "sale_deed"},
    ]
    result = validate_required_documents(docs)
    assert result["is_valid"] is False
    assert result["has_aadhaar"] is True
    assert result["has_pan"] is False
    assert "pan" in result["missing_documents"]
    assert "aadhaar" not in result["missing_documents"]
    assert "PAN Card" in result["missing_labels"]
    assert "PAN" in result["message"]


# ─── 4. Both missing: fails and identifies both ─────────────────────────────

def test_both_missing_fails_and_identifies_both():
    """When both Aadhaar and PAN are missing, fails and identifies both."""
    docs = [
        {"document_type": "salary_slip"},
        {"document_type": "sale_deed"},
        {"document_type": "site_front_view"},
    ]
    result = validate_required_documents(docs)
    assert result["is_valid"] is False
    assert result["has_aadhaar"] is False
    assert result["has_pan"] is False
    assert "aadhaar" in result["missing_documents"]
    assert "pan" in result["missing_documents"]
    assert "Aadhaar Card" in result["missing_labels"]
    assert "PAN Card" in result["missing_labels"]
    assert "Aadhaar" in result["message"] and "PAN" in result["message"]


def test_empty_documents_fails_and_identifies_both():
    """When no documents have been uploaded, fails and identifies both."""
    result = validate_required_documents([])
    assert result["is_valid"] is False
    assert "aadhaar" in result["missing_documents"]
    assert "pan" in result["missing_documents"]
    assert "Aadhaar" in result["message"] and "PAN" in result["message"]


# ─── 5. Other documents missing: validation STILL passes ─────────────────────

def test_other_documents_missing_still_passes_when_aadhaar_and_pan_present():
    """When Aadhaar and PAN are present, absence of salary_slip, sale_deed, tax_receipt,

    bank_statement, or site images MUST NOT cause required-document validation to fail.
    """
    # ONLY Aadhaar and PAN uploaded, nothing else
    minimal_docs = [
        {"document_type": "aadhaar"},
        {"document_type": "pan"},
    ]
    result = validate_required_documents(minimal_docs)
    assert result["is_valid"] is True
    assert result["missing_documents"] == []
    assert result["missing_labels"] == []


def test_joint_applicant_docs_do_not_substitute_primary_mandatory_docs():
    """Joint applicant docs alone must not satisfy primary applicant requirements."""
    docs = [
        {"document_type": "aadhaar", "joint_applicant_index": 1},
        {"document_type": "pan", "joint_applicant_index": 1},
    ]
    result = validate_required_documents(docs)
    # Primary aadhaar and pan are still missing
    assert result["is_valid"] is False
    assert "aadhaar" in result["missing_documents"]
    assert "pan" in result["missing_documents"]


# ─── 6. VerificationEngine _check_required_docs integration ──────────────────

def test_verification_engine_passes_with_only_aadhaar_and_pan():
    """VerificationEngine._check_required_docs must pass with score 1.0 when only

    Aadhaar and PAN are uploaded, even when income/collateral docs are missing.
    """
    engine = VerificationEngine()
    docs = [
        {"document_type": "aadhaar", "id": 1},
        {"document_type": "pan", "id": 2},
    ]
    res = engine._check_required_docs(docs)
    assert res["passed"] is True
    assert res["score"] == 1.0
    assert "Mandatory documents" in res["details"]


def test_verification_engine_fails_when_aadhaar_missing():
    engine = VerificationEngine()
    docs = [{"document_type": "pan", "id": 2}]
    res = engine._check_required_docs(docs)
    assert res["passed"] is False
    assert "Aadhaar Card" in res["details"]


def test_verification_engine_fails_when_pan_missing():
    engine = VerificationEngine()
    docs = [{"document_type": "aadhaar", "id": 1}]
    res = engine._check_required_docs(docs)
    assert res["passed"] is False
    assert "PAN Card" in res["details"]


# ─── 7. FraudDetector missing critical documents check ───────────────────────

def test_fraud_detector_does_not_flag_missing_salary_slip_as_missing_required():
    """FraudDetector must NOT penalize or alert 'Missing required documents' when

    only optional documents (e.g. salary slip) are missing, as long as Aadhaar and PAN are present.
    """
    detector = FraudDetector()
    docs = [
        {"document_type": "aadhaar"},
        {"document_type": "pan"},
    ]
    # Synthetic extracted info
    info = {
        "applicant_name": "Sunil Kumar",
        "aadhaar_number": "999988887777",
        "pan_number": "ABCDE1234F",
    }
    # Mock DB session
    class DummyDB:
        def query(self, *args, **kwargs):
            class DummyQuery:
                def filter(self, *a, **k):
                    class DummyFilter:
                        def all(self):
                            return []
                    return DummyFilter()
            return DummyQuery()

    res = detector.analyse(info, docs, application_id=1, db=DummyDB())
    # Should not have any alert mentioning "Missing required documents"
    missing_alerts = [a for a in res["alerts"] if "Missing required documents" in a]
    assert len(missing_alerts) == 0, f"Unexpected missing document alert: {missing_alerts}"


def test_fraud_detector_flags_when_aadhaar_or_pan_missing():
    """FraudDetector must flag when Aadhaar or PAN is missing."""
    detector = FraudDetector()
    docs = [{"document_type": "salary_slip"}]  # Neither Aadhaar nor PAN
    info = {"applicant_name": "Sunil Kumar"}

    class DummyDB:
        def query(self, *args, **kwargs):
            class DummyQuery:
                def filter(self, *a, **k):
                    class DummyFilter:
                        def all(self):
                            return []
                    return DummyFilter()
            return DummyQuery()

    res = detector.analyse(info, docs, application_id=1, db=DummyDB())
    missing_alerts = [a for a in res["alerts"] if "Missing required documents" in a]
    assert len(missing_alerts) == 1
    assert "Aadhaar Card" in missing_alerts[0]
    assert "PAN Card" in missing_alerts[0]


# ─── 8. GET /documents/validate-required/{application_id} API endpoint ───────

from fastapi.testclient import TestClient
from app.main import app
from app.core.security import create_access_token
from app.db.database import SessionLocal
from app.models.user import User
from app.models.application import Application
from app.models.document import Document


def test_api_validate_required_nonexistent_application():
    """Verify endpoint returns 404 for missing / non-existent application ID."""
    db = SessionLocal()
    try:
        user = db.query(User).first()
        token = create_access_token({"sub": str(user.id), "role": user.role})
        client = TestClient(app)
        res = client.get("/documents/validate-required/9999999", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 404
        assert "Application not found" in res.json().get("detail", "")
    finally:
        db.close()


def test_api_validate_required_invalid_id_format():
    """Verify endpoint handles invalid non-integer application ID safely (422)."""
    db = SessionLocal()
    try:
        user = db.query(User).first()
        token = create_access_token({"sub": str(user.id), "role": user.role})
        client = TestClient(app)
        res = client.get("/documents/validate-required/invalid_id", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 422
    finally:
        db.close()


def test_api_validate_required_isolation_between_applications():
    """Verify endpoint checks document records belonging ONLY to the queried application.

    Application A has Aadhaar + PAN.
    Application B has no documents.
    Endpoint for A must return is_valid: True.
    Endpoint for B must return is_valid: False.
    """
    db = SessionLocal()
    app_a = None
    app_b = None
    try:
        admin = db.query(User).filter(User.role == "admin").first() or db.query(User).first()
        token = create_access_token({"sub": str(admin.id), "role": admin.role})
        headers = {"Authorization": f"Bearer {token}"}
        client = TestClient(app)

        # Create isolated Application A
        app_a = Application(
            user_id=admin.id,
            applicant_name="App A Primary",
            loan_amount=200000.0,
            loan_type="Personal Loan"
        )
        # Create isolated Application B
        app_b = Application(
            user_id=admin.id,
            applicant_name="App B Primary",
            loan_amount=300000.0,
            loan_type="Home Loan"
        )
        db.add_all([app_a, app_b])
        db.commit()
        db.refresh(app_a)
        db.refresh(app_b)

        # Upload Aadhaar & PAN only to App A
        doc_aadhaar = Document(application_id=app_a.id, document_type=DocumentType.aadhaar, file_path="dummy_aadhaar.png")
        doc_pan = Document(application_id=app_a.id, document_type=DocumentType.pan, file_path="dummy_pan.png")
        # App B has a joint applicant Aadhaar only (should not satisfy primary)
        doc_b_joint = Document(application_id=app_b.id, document_type=DocumentType.aadhaar, joint_applicant_index=1, file_path="dummy_joint.png")

        db.add_all([doc_aadhaar, doc_pan, doc_b_joint])
        db.commit()

        # Query App A: Must be valid
        res_a = client.get(f"/documents/validate-required/{app_a.id}", headers=headers)
        assert res_a.status_code == 200
        data_a = res_a.json()
        assert data_a["application_id"] == app_a.id
        assert data_a["is_valid"] is True
        assert data_a["has_aadhaar"] is True
        assert data_a["has_pan"] is True
        assert len(data_a["missing_documents"]) == 0

        # Query App B: Must NOT be valid (joint doc does not substitute for primary)
        res_b = client.get(f"/documents/validate-required/{app_b.id}", headers=headers)
        assert res_b.status_code == 200
        data_b = res_b.json()
        assert data_b["application_id"] == app_b.id
        assert data_b["is_valid"] is False
        assert "aadhaar" in data_b["missing_documents"]
        assert "pan" in data_b["missing_documents"]

    finally:
        # Cleanup isolated test data
        if app_a:
            db.query(Document).filter(Document.application_id == app_a.id).delete()
            db.query(Application).filter(Application.id == app_a.id).delete()
        if app_b:
            db.query(Document).filter(Document.application_id == app_b.id).delete()
            db.query(Application).filter(Application.id == app_b.id).delete()
        db.commit()
        db.close()

