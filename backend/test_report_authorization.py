"""
Offline regression tests for SmartVerify report endpoint authorization.
Verifies:
1. Reused authorize_application_access policy on GET /{app_id}, GET /{app_id}/download, POST /{app_id}/regenerate-pdf
2. Admin access allowed across all applications
3. Assigned loan officer access allowed for owned application
4. Unauthorized loan officer access denied with HTTP 403
5. Nonexistent applications return HTTP 404
6. Nonexistent reports return HTTP 404
7. Phase 8 & 9 review/edit/evidence/revalidate/generate-pdf routes enforce application authorization
8. Static /reports mount has been removed (no unauthenticated file exposure)

Safety: Zero live external API calls (Gemini/Groq/Supabase). Mocked and offline with synthetic data.
"""

import io
import os
import tempfile
from unittest.mock import patch, MagicMock
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
from app.models.application import Application
from app.models.verification_report import VerificationReport
from app.api.endpoints import reports


@pytest.fixture(scope="module")
def report_test_env():
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
        id=101,
        name="Admin User",
        email="admin@bank.com",
        password="hashedpassword",
        role=UserRole.admin,
    )
    assigned_officer = User(
        id=102,
        name="Assigned Officer",
        email="officer_assigned@bank.com",
        password="hashedpassword",
        role=UserRole.loan_officer,
    )
    unauthorized_officer = User(
        id=103,
        name="Unauthorized Officer",
        email="officer_unauth@bank.com",
        password="hashedpassword",
        role=UserRole.loan_officer,
    )
    db.add(admin)
    db.add(assigned_officer)
    db.add(unauthorized_officer)

    # Application 501 owned by assigned_officer (102) with a report
    app_with_report = Application(
        id=501,
        user_id=102,
        applicant_name="Sunil Mehta",
        aadhaar_number="123456789012",
        pan_number="ABCDE1234F",
        branch="Main Branch",
        loan_type="Personal Loan",
        loan_amount=250000.0,
        loan_tenure=24,
        interest_rate=11.0,
    )
    db.add(app_with_report)

    # Application 502 owned by assigned_officer (102) WITHOUT a report
    app_without_report = Application(
        id=502,
        user_id=102,
        applicant_name="Deepa Sharma",
        aadhaar_number="234567890123",
        pan_number="BCDEF2345G",
        branch="Main Branch",
        loan_type="Home Loan",
        loan_amount=1500000.0,
        loan_tenure=120,
        interest_rate=8.5,
    )
    db.add(app_without_report)

    report_record = VerificationReport(
        id=501,
        application_id=501,
        verification_score=88.5,
        risk_score=11.5,
        fraud_flag=False,
        status="approved",
        extracted_info={"applicant_name": "Sunil Mehta"},
        fraud_analysis={"risk_level": "LOW"},
        verification_details={"identity": "VERIFIED"},
        pdf_path="/tmp/fake_report_501.pdf",
    )
    db.add(report_record)
    db.commit()

    active_user = [assigned_officer]

    def override_get_db():
        try:
            yield db
        finally:
            pass

    def override_get_current_user():
        return active_user[0]

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    client = TestClient(app)

    yield {
        "client": client,
        "db": db,
        "active_user": active_user,
        "admin": admin,
        "assigned_officer": assigned_officer,
        "unauthorized_officer": unauthorized_officer,
        "app_id": 501,
        "app_id_no_report": 502,
        "nonexistent_app_id": 99999,
    }

    app.dependency_overrides.clear()
    reports._review_sessions.clear()


# ─────────────────────────────────────────────────────────────────────────────
# 1. GET /report/{application_id}
# ─────────────────────────────────────────────────────────────────────────────

def test_get_report_unauthorized_loan_officer_forbidden(report_test_env):
    env = report_test_env
    env["active_user"][0] = env["unauthorized_officer"]
    res = env["client"].get(f"/report/{env['app_id']}")
    assert res.status_code == 403
    assert res.json()["detail"] == "Access denied"


def test_get_report_authorized_loan_officer_success(report_test_env):
    env = report_test_env
    env["active_user"][0] = env["assigned_officer"]
    res = env["client"].get(f"/report/{env['app_id']}")
    assert res.status_code == 200
    data = res.json()
    assert data["verification_score"] == 88.5
    assert data["status"] == "approved"


def test_get_report_admin_success(report_test_env):
    env = report_test_env
    env["active_user"][0] = env["admin"]
    res = env["client"].get(f"/report/{env['app_id']}")
    assert res.status_code == 200
    data = res.json()
    assert data["verification_score"] == 88.5
    assert data["status"] == "approved"


def test_get_report_nonexistent_application_not_found(report_test_env):
    env = report_test_env
    env["active_user"][0] = env["assigned_officer"]
    res = env["client"].get(f"/report/{env['nonexistent_app_id']}")
    assert res.status_code == 404
    assert res.json()["detail"] == "Application not found"


def test_get_report_application_exists_report_missing_not_found(report_test_env):
    env = report_test_env
    env["active_user"][0] = env["assigned_officer"]
    res = env["client"].get(f"/report/{env['app_id_no_report']}")
    assert res.status_code == 404
    assert res.json()["detail"] == "Report not found"


# ─────────────────────────────────────────────────────────────────────────────
# 2. GET /report/{application_id}/download
# ─────────────────────────────────────────────────────────────────────────────

def test_download_report_unauthorized_loan_officer_forbidden(report_test_env):
    env = report_test_env
    env["active_user"][0] = env["unauthorized_officer"]
    res = env["client"].get(f"/report/{env['app_id']}/download")
    assert res.status_code == 403
    assert res.json()["detail"] == "Access denied"


def test_download_report_authorized_loan_officer_success(report_test_env):
    env = report_test_env
    env["active_user"][0] = env["assigned_officer"]

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_pdf:
        tmp_pdf.write(b"%PDF-1.4 Mock PDF Content")
        tmp_pdf_path = tmp_pdf.name

    try:
        with patch("app.api.endpoints.reports.generate_report", return_value=tmp_pdf_path):
            res = env["client"].get(f"/report/{env['app_id']}/download")
            assert res.status_code == 200
            assert res.headers["content-type"] == "application/pdf"
            assert res.content.startswith(b"%PDF-1.4")
    finally:
        if os.path.exists(tmp_pdf_path):
            os.remove(tmp_pdf_path)


def test_download_report_admin_success(report_test_env):
    env = report_test_env
    env["active_user"][0] = env["admin"]

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_pdf:
        tmp_pdf.write(b"%PDF-1.4 Admin Mock PDF Content")
        tmp_pdf_path = tmp_pdf.name

    try:
        with patch("app.api.endpoints.reports.generate_report", return_value=tmp_pdf_path):
            res = env["client"].get(f"/report/{env['app_id']}/download")
            assert res.status_code == 200
            assert res.headers["content-type"] == "application/pdf"
    finally:
        if os.path.exists(tmp_pdf_path):
            os.remove(tmp_pdf_path)


def test_download_report_nonexistent_application_not_found(report_test_env):
    env = report_test_env
    env["active_user"][0] = env["assigned_officer"]
    res = env["client"].get(f"/report/{env['nonexistent_app_id']}/download")
    assert res.status_code == 404
    assert res.json()["detail"] == "Application not found"


def test_download_report_missing_report_not_found(report_test_env):
    env = report_test_env
    env["active_user"][0] = env["assigned_officer"]
    res = env["client"].get(f"/report/{env['app_id_no_report']}/download")
    assert res.status_code == 404
    assert res.json()["detail"] == "Report not found"


# ─────────────────────────────────────────────────────────────────────────────
# 3. POST /report/{application_id}/regenerate-pdf
# ─────────────────────────────────────────────────────────────────────────────

def test_regenerate_pdf_unauthorized_loan_officer_forbidden(report_test_env):
    env = report_test_env
    env["active_user"][0] = env["unauthorized_officer"]
    res = env["client"].post(f"/report/{env['app_id']}/regenerate-pdf")
    assert res.status_code == 403
    assert res.json()["detail"] == "Access denied"


def test_regenerate_pdf_authorized_loan_officer_success(report_test_env):
    env = report_test_env
    env["active_user"][0] = env["assigned_officer"]

    with patch("app.api.endpoints.reports.generate_report", return_value="/tmp/regenerated_501.pdf"):
        res = env["client"].post(f"/report/{env['app_id']}/regenerate-pdf")
        assert res.status_code == 200
        assert res.json()["message"] == "PDF regenerated successfully"
        assert res.json()["pdf_path"] == "/tmp/regenerated_501.pdf"


def test_regenerate_pdf_admin_success(report_test_env):
    env = report_test_env
    env["active_user"][0] = env["admin"]

    with patch("app.api.endpoints.reports.generate_report", return_value="/tmp/admin_regenerated_501.pdf"):
        res = env["client"].post(f"/report/{env['app_id']}/regenerate-pdf")
        assert res.status_code == 200
        assert res.json()["message"] == "PDF regenerated successfully"


def test_regenerate_pdf_nonexistent_application_not_found(report_test_env):
    env = report_test_env
    env["active_user"][0] = env["assigned_officer"]
    res = env["client"].post(f"/report/{env['nonexistent_app_id']}/regenerate-pdf")
    assert res.status_code == 404
    assert res.json()["detail"] == "Application not found"


def test_regenerate_pdf_missing_report_not_found(report_test_env):
    env = report_test_env
    env["active_user"][0] = env["assigned_officer"]
    res = env["client"].post(f"/report/{env['app_id_no_report']}/regenerate-pdf")
    assert res.status_code == 404
    assert "Run AI verification first" in res.json()["detail"]


# ─────────────────────────────────────────────────────────────────────────────
# 4. Phase 8 & 9 Review / Edit / Evidence / Revalidate / Generate-PDF Authorization
# ─────────────────────────────────────────────────────────────────────────────

def test_get_report_review_unauthorized_loan_officer_forbidden(report_test_env):
    env = report_test_env
    env["active_user"][0] = env["unauthorized_officer"]
    res = env["client"].get(f"/report/{env['app_id']}/review")
    assert res.status_code == 403
    assert res.json()["detail"] == "Access denied"


def test_get_report_review_nonexistent_application_not_found(report_test_env):
    env = report_test_env
    env["active_user"][0] = env["assigned_officer"]
    res = env["client"].get(f"/report/{env['nonexistent_app_id']}/review")
    assert res.status_code == 404
    assert res.json()["detail"] == "Application not found"


def test_review_action_endpoints_unauthorized_loan_officer_forbidden(report_test_env):
    env = report_test_env
    app_id = env["app_id"]

    # Seed an in-memory session for app 501
    mock_state = MagicMock()
    mock_state.can_export_pdf = True
    reports._review_sessions[app_id] = {
        "state": mock_state,
        "context": MagicMock(),
        "evidence_map": MagicMock(),
        "template": MagicMock(),
        "baseline": MagicMock(),
        "validated": MagicMock(),
        "user_id": env["assigned_officer"].id,
    }

    env["active_user"][0] = env["unauthorized_officer"]

    # 1. Edit particular review
    edit_payload = {
        "particular_id": "test_part",
        "new_text": "Updated text",
        "edited_by": "unauthorized_officer",
    }
    res_edit = env["client"].post(f"/report/{app_id}/review/edit", json=edit_payload)
    assert res_edit.status_code == 403
    assert res_edit.json()["detail"] == "Access denied"

    # 2. Toggle evidence
    ev_payload = {"particular_id": "test_part", "evidence_id": "ev_1", "included": False}
    res_ev = env["client"].post(f"/report/{app_id}/review/evidence", json=ev_payload)
    assert res_ev.status_code == 403
    assert res_ev.json()["detail"] == "Access denied"

    # 3. Revalidate review
    res_reval = env["client"].post(f"/report/{app_id}/review/revalidate")
    assert res_reval.status_code == 403
    assert res_reval.json()["detail"] == "Access denied"

    # 4. Generate approved PDF
    res_pdf = env["client"].post(f"/report/{app_id}/generate-pdf")
    assert res_pdf.status_code == 403
    assert res_pdf.json()["detail"] == "Access denied"

    # Clean up session
    reports._review_sessions.pop(app_id, None)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Static Mount Removal
# ─────────────────────────────────────────────────────────────────────────────

def test_static_reports_mount_is_removed(report_test_env):
    env = report_test_env
    # 1. Verify no static mount named 'reports' exists in FastAPI routes
    route_names = [getattr(r, "name", None) for r in app.routes]
    assert "reports" not in route_names

    # 2. Verify an unauthenticated request to /reports/ cannot serve PDF files from REPORT_DIR
    secret_pdf_path = os.path.join(settings.REPORT_DIR, "test_secret_report.pdf")
    try:
        with open(secret_pdf_path, "wb") as f:
            f.write(b"%PDF-1.4 Secret Applicant Financial Data")

        res = env["client"].get("/reports/test_secret_report.pdf")
        # Ensure the actual PDF was NOT served
        assert res.headers.get("content-type") != "application/pdf"
        assert b"Secret Applicant Financial Data" not in res.content
    finally:
        if os.path.exists(secret_pdf_path):
            os.remove(secret_pdf_path)
