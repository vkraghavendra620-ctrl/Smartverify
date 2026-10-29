"""
Offline regression tests for the four targeted security & reliability fixes:
1. Protected uploads: /uploads/{filename} and /documents/{id}/file require authentication and ownership
2. PII removal from error logs: CrewAI JSON parsing errors never log raw applicant text
3. Supabase config fallback: explicit empty strings raise StorageConfigurationError
4. Secured orphaned screenshot deletion: missing applications require admin privileges
"""

import io
import os
import json
import logging
import tempfile
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
from app.models.document import Document, DocumentType
from app.models.government_screenshot import GovernmentVerificationScreenshot
from app.services.storage_service import (
    SupabaseStorageService,
    StorageConfigurationError,
)
from app.agents.crew import LoanVerificationCrew


@pytest.fixture(scope="module")
def security_test_env():
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
        id=601,
        name="Security Admin",
        email="sec_admin@bank.com",
        password="hashedpassword",
        role=UserRole.admin,
    )
    assigned_officer = User(
        id=602,
        name="Assigned Officer",
        email="sec_assigned@bank.com",
        password="hashedpassword",
        role=UserRole.loan_officer,
    )
    unauthorized_officer = User(
        id=603,
        name="Unauthorized Officer",
        email="sec_unauth@bank.com",
        password="hashedpassword",
        role=UserRole.loan_officer,
    )
    db.add(admin)
    db.add(assigned_officer)
    db.add(unauthorized_officer)

    # Application 701 owned by assigned_officer (602)
    app_record = Application(
        id=701,
        user_id=602,
        applicant_name="Ramesh Patel",
        branch="Ahmedabad Main",
        loan_type="Personal Loan",
        loan_amount=300000.0,
        loan_tenure=36,
        interest_rate=10.5,
    )
    db.add(app_record)

    # Document on local disk
    doc_filename = "test_pan_701.png"
    doc_disk_path = os.path.join(settings.UPLOAD_DIR, doc_filename)
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    with open(doc_disk_path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\nFake PAN Image Content")

    doc_record = Document(
        id=701,
        application_id=701,
        document_type=DocumentType.pan,
        file_path=doc_disk_path,
        original_name="pan_card.png",
    )
    db.add(doc_record)

    # Normal gov screenshot belonging to app 701
    shot_filename = "gov_uidai_701.png"
    shot_disk_path = os.path.join(settings.UPLOAD_DIR, shot_filename)
    with open(shot_disk_path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\nFake UIDAI Screenshot")

    shot_record = GovernmentVerificationScreenshot(
        id=801,
        application_id=701,
        verification_type="uidai_aadhaar",
        government_portal="UIDAI",
        screenshot_path=shot_disk_path,
        notes="UIDAI Screenshot",
    )
    db.add(shot_record)

    # Orphaned screenshot: application 9999 does not exist in DB
    orphaned_shot_filename = "gov_orphaned_9999.png"
    orphaned_disk_path = os.path.join(settings.UPLOAD_DIR, orphaned_shot_filename)
    with open(orphaned_disk_path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\nFake Orphaned Screenshot")

    orphaned_shot = GovernmentVerificationScreenshot(
        id=802,
        application_id=9999,
        verification_type="pan_aadhaar_link",
        government_portal="Income Tax e-Filing",
        screenshot_path=orphaned_disk_path,
        notes="Orphaned Screenshot",
    )
    db.add(orphaned_shot)
    db.commit()

    active_user = [assigned_officer]

    def override_get_db():
        try:
            yield db
        finally:
            pass

    def override_get_current_user():
        if active_user[0] is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=401, detail="Not authenticated")
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
        "app_id": 701,
        "doc_id": 701,
        "doc_filename": doc_filename,
        "shot_id": 801,
        "shot_filename": shot_filename,
        "orphaned_shot_id": 802,
        "doc_disk_path": doc_disk_path,
        "shot_disk_path": shot_disk_path,
        "orphaned_disk_path": orphaned_disk_path,
    }

    app.dependency_overrides.clear()
    for path in (doc_disk_path, shot_disk_path, orphaned_disk_path):
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1 Tests: Protected Uploads & Documents
# ─────────────────────────────────────────────────────────────────────────────

def test_unauthenticated_cannot_retrieve_upload_by_filename(security_test_env):
    env = security_test_env
    env["active_user"][0] = None  # Unauthenticated
    res = env["client"].get(f"/uploads/{env['doc_filename']}")
    assert res.status_code == 401


def test_unauthorized_officer_cannot_retrieve_upload_by_filename(security_test_env):
    env = security_test_env
    env["active_user"][0] = env["unauthorized_officer"]
    res = env["client"].get(f"/uploads/{env['doc_filename']}")
    assert res.status_code == 403
    assert res.json()["detail"] == "Access denied"


def test_authorized_officer_can_retrieve_upload_by_filename(security_test_env):
    env = security_test_env
    env["active_user"][0] = env["assigned_officer"]
    res = env["client"].get(f"/uploads/{env['doc_filename']}")
    assert res.status_code == 200
    assert b"Fake PAN Image Content" in res.content


def test_admin_can_retrieve_upload_by_filename(security_test_env):
    env = security_test_env
    env["active_user"][0] = env["admin"]
    res = env["client"].get(f"/uploads/{env['doc_filename']}")
    assert res.status_code == 200
    assert b"Fake PAN Image Content" in res.content


def test_upload_path_traversal_rejected(security_test_env):
    env = security_test_env
    env["active_user"][0] = env["assigned_officer"]
    res = env["client"].get("/uploads/..%2F..%2Fmain.py")
    assert res.status_code in (400, 404)


def test_document_endpoint_file_authorization(security_test_env):
    env = security_test_env
    doc_id = env["doc_id"]

    # 1. Unauthenticated -> 401
    env["active_user"][0] = None
    res_unauth = env["client"].get(f"/documents/{doc_id}/file")
    assert res_unauth.status_code == 401

    # 2. Unauthorized officer -> 403
    env["active_user"][0] = env["unauthorized_officer"]
    res_forbidden = env["client"].get(f"/documents/{doc_id}/file")
    assert res_forbidden.status_code == 403
    assert res_forbidden.json()["detail"] == "Access denied"

    # 3. Authorized officer -> 200
    env["active_user"][0] = env["assigned_officer"]
    res_ok = env["client"].get(f"/documents/{doc_id}/file")
    assert res_ok.status_code == 200
    assert b"Fake PAN Image Content" in res_ok.content

    # 4. Admin -> 200
    env["active_user"][0] = env["admin"]
    res_admin = env["client"].get(f"/documents/{doc_id}/file")
    assert res_admin.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# Fix 2 Tests: Remove PII from Error Logs in CrewAI
# ─────────────────────────────────────────────────────────────────────────────

def test_crewai_json_parse_error_does_not_log_pii(caplog):
    crew = LoanVerificationCrew(db_session=None)

    sensitive_pii_payload = (
        "Here is the result: "
        "{'applicant_name': 'SECRET_APPLICANT_RAJESH', "
        "'pan_number': 'ABCDE9999Z', "
        "'aadhaar_number': '9999-8888-7777', "
        "'salary': 250000.0, "
        "malformed_syntax_no_quotes}"
    )

    caplog.clear()
    caplog.set_level(logging.ERROR)
    parsed = crew._parse_final_json(sensitive_pii_payload)
    assert parsed == {}

    captured_logs = caplog.text
    # Diagnostic metadata should be logged
    assert "[CrewAI Output Parse Error]" in captured_logs or "[CrewAI Output Error]" in captured_logs
    # Sensitive applicant PII MUST NEVER appear in captured logs
    assert "SECRET_APPLICANT_RAJESH" not in captured_logs
    assert "ABCDE9999Z" not in captured_logs
    assert "9999-8888-7777" not in captured_logs
    assert "250000" not in captured_logs


# ─────────────────────────────────────────────────────────────────────────────
# Fix 3 Tests: Correct Supabase Configuration Fallback
# ─────────────────────────────────────────────────────────────────────────────

def test_supabase_storage_explicit_empty_string_raises():
    # Explicit empty strings must not fall back to settings
    with pytest.raises(StorageConfigurationError):
        SupabaseStorageService(supabase_url="", service_role_key="")

    with pytest.raises(StorageConfigurationError):
        SupabaseStorageService(supabase_url="https://test.supabase.co", service_role_key="")

    with pytest.raises(StorageConfigurationError):
        SupabaseStorageService(supabase_url="", service_role_key="some_key")


# ─────────────────────────────────────────────────────────────────────────────
# Fix 4 Tests: Secure Orphaned Screenshot Deletion
# ─────────────────────────────────────────────────────────────────────────────

def test_delete_gov_screenshot_assigned_officer_success(security_test_env):
    env = security_test_env
    env["active_user"][0] = env["assigned_officer"]

    # Re-seed a screenshot for testing deletion
    db = env["db"]
    shot_path = os.path.join(settings.UPLOAD_DIR, "shot_del_assigned.png")
    with open(shot_path, "wb") as f:
        f.write(b"Temp Shot")

    shot = GovernmentVerificationScreenshot(
        id=810,
        application_id=env["app_id"],
        verification_type="uidai_aadhaar",
        government_portal="UIDAI",
        screenshot_path=shot_path,
    )
    db.add(shot)
    db.commit()

    res = env["client"].delete(f"/gov-screenshots/{shot.id}")
    assert res.status_code == 200
    assert res.json()["status"] == "success"
    assert not os.path.exists(shot_path)


def test_delete_gov_screenshot_unauthorized_officer_forbidden(security_test_env):
    env = security_test_env
    env["active_user"][0] = env["unauthorized_officer"]

    res = env["client"].delete(f"/gov-screenshots/{env['shot_id']}")
    assert res.status_code == 403
    assert res.json()["detail"] == "Access denied"


def test_delete_orphaned_screenshot_unauthorized_officer_forbidden(security_test_env):
    env = security_test_env
    # Orphaned screenshot (app 9999 does not exist).
    # Loan officers must be forbidden from deleting orphaned screenshots.
    env["active_user"][0] = env["unauthorized_officer"]
    res = env["client"].delete(f"/gov-screenshots/{env['orphaned_shot_id']}")
    assert res.status_code == 403
    assert "Administrator privileges required" in res.json()["detail"]


def test_delete_orphaned_screenshot_admin_success(security_test_env):
    env = security_test_env
    # Administrators are allowed to clean up orphaned screenshots
    env["active_user"][0] = env["admin"]
    res = env["client"].delete(f"/gov-screenshots/{env['orphaned_shot_id']}")
    assert res.status_code == 200
    assert res.json()["status"] == "success"
    assert not os.path.exists(env["orphaned_disk_path"])
