"""
SmartVerify Phase 2B Deployment Readiness Tests
Covers:
1. Fix 1: Controlled demo user seeding (opt-in) & secure initial admin bootstrap.
2. Fix 2: Preventing public admin registration & enforcing authorized admin user creation.
3. Fix 3: PostgreSQL URL normalization (postgres:// -> postgresql://) and Alembic offline SQL generation.
"""

import io
import os
import sys
import logging
import pytest
from contextlib import redirect_stdout
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base, get_db
from app.models.user import User, UserRole
from app.core.config import settings, normalize_database_url
from app.core.security import verify_password, create_access_token
from app.main import app, ensure_default_users
import alembic.config
from alembic import command


@pytest.fixture
def phase2b_db(tmp_path, monkeypatch):
    """Isolated SQLite database fixture for Phase 2B tests."""
    db_file = tmp_path / "test_phase2b.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("app.db.database.SessionLocal", TestingSessionLocal)

    client = TestClient(app)

    yield {"engine": engine, "SessionLocal": TestingSessionLocal, "client": client}

    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1: Default Insecure Credentials Removal & Safe Bootstrap
# ─────────────────────────────────────────────────────────────────────────────

def test_default_startup_does_not_seed_demo_users(phase2b_db, monkeypatch):
    """By default, ensure_default_users() must NOT create any hardcoded demo accounts."""
    session = phase2b_db["SessionLocal"]()
    monkeypatch.setattr(settings, "SEED_DEMO_USERS", False)
    monkeypatch.setattr(settings, "INITIAL_ADMIN_EMAIL", "")
    monkeypatch.setattr(settings, "INITIAL_ADMIN_PASSWORD", "")

    # Execute startup seeding hook
    ensure_default_users()

    # Verify zero users exist
    user_count = session.query(User).count()
    assert user_count == 0, f"Expected 0 users, found {user_count}"
    session.close()


def test_demo_seeding_opt_in_creates_demo_users(phase2b_db, monkeypatch):
    """When explicitly opted-in via SEED_DEMO_USERS=True, demo accounts are seeded."""
    session = phase2b_db["SessionLocal"]()
    monkeypatch.setattr(settings, "SEED_DEMO_USERS", True)
    monkeypatch.setattr(settings, "INITIAL_ADMIN_EMAIL", "")
    monkeypatch.setattr(settings, "INITIAL_ADMIN_PASSWORD", "")

    ensure_default_users()

    admin = session.query(User).filter(User.email == "admin@smartverify.com").first()
    officer = session.query(User).filter(User.email == "officer@smartverify.com").first()

    assert admin is not None
    assert admin.role in (UserRole.admin, "admin")
    assert verify_password("admin123", admin.password)

    assert officer is not None
    assert officer.role in (UserRole.loan_officer, "loan_officer")
    assert verify_password("officer123", officer.password)
    session.close()


def test_initial_admin_bootstrap_creates_admin_without_logging_secrets(phase2b_db, monkeypatch, caplog):
    """Initial administrator is safely bootstrapped from environment variables without logging passwords."""
    session = phase2b_db["SessionLocal"]()
    secret_pass = "SuperSecure#2026!KeyPhrase"
    admin_email = "bootstrap_admin@bank.com"

    monkeypatch.setattr(settings, "SEED_DEMO_USERS", False)
    monkeypatch.setattr(settings, "INITIAL_ADMIN_EMAIL", admin_email)
    monkeypatch.setattr(settings, "INITIAL_ADMIN_PASSWORD", secret_pass)

    with caplog.at_level(logging.INFO):
        ensure_default_users()

    admin = session.query(User).filter(User.email == admin_email).first()
    assert admin is not None
    assert admin.role in (UserRole.admin, "admin")
    assert verify_password(secret_pass, admin.password)

    # Password must NEVER appear in logs
    for record in caplog.records:
        assert secret_pass not in record.message
        assert "password" not in record.message.lower() or "admin123" not in record.message

    # Ensure demo accounts were not created
    demo_admin = session.query(User).filter(User.email == "admin@smartverify.com").first()
    assert demo_admin is None
    session.close()


def test_existing_users_preserved_and_not_locked_out(phase2b_db, monkeypatch):
    """Repeated startup runs must never lock out or overwrite existing users."""
    session = phase2b_db["SessionLocal"]()
    custom_admin = User(
        name="Existing Admin",
        email="existing@bank.com",
        password="hashed_custom_password",
        role=UserRole.admin,
    )
    session.add(custom_admin)
    session.commit()

    monkeypatch.setattr(settings, "SEED_DEMO_USERS", False)
    monkeypatch.setattr(settings, "INITIAL_ADMIN_EMAIL", "existing@bank.com")
    monkeypatch.setattr(settings, "INITIAL_ADMIN_PASSWORD", "new_attempted_pass")

    ensure_default_users()

    refreshed = session.query(User).filter(User.email == "existing@bank.com").first()
    assert refreshed.password == "hashed_custom_password"  # Unchanged
    session.close()


# ─────────────────────────────────────────────────────────────────────────────
# Fix 2: Public Registration Protection & Authorized Administrator Creation
# ─────────────────────────────────────────────────────────────────────────────

def test_public_registration_rejects_admin_role(phase2b_db):
    """Public registration must reject client attempts to choose admin role with HTTP 400."""
    client = phase2b_db["client"]
    res = client.post("/auth/register", json={
        "name": "Attacker",
        "email": "attacker@test.com",
        "password": "Password123!",
        "role": "admin"
    })
    assert res.status_code == 400
    assert "administrator" in res.json()["detail"].lower()


def test_public_registration_rejects_privileged_roles(phase2b_db):
    """Public registration must reject other privileged or invalid roles."""
    client = phase2b_db["client"]
    res = client.post("/auth/register", json={
        "name": "Attacker",
        "email": "attacker2@test.com",
        "password": "Password123!",
        "role": "superuser"
    })
    assert res.status_code == 400


def test_public_registration_creates_loan_officer(phase2b_db):
    """Legitimate public registration succeeds and strictly enforces loan_officer role."""
    client = phase2b_db["client"]
    res = client.post("/auth/register", json={
        "name": "Legitimate Officer",
        "email": "officer_legit@test.com",
        "password": "Password123!",
        "role": "loan_officer"
    })
    assert res.status_code == 201
    data = res.json()
    assert data["email"] == "officer_legit@test.com"
    assert data["role"] == "loan_officer"

    # Also verify registration works when role is omitted
    res2 = client.post("/auth/register", json={
        "name": "Legitimate Officer 2",
        "email": "officer_legit2@test.com",
        "password": "Password123!",
    })
    assert res2.status_code == 201
    assert res2.json()["role"] == "loan_officer"


def test_admin_create_user_unauthenticated_rejected(phase2b_db):
    """Unauthenticated users cannot access the authorized admin user creation workflow."""
    client = phase2b_db["client"]
    res = client.post("/auth/admin/create-user", json={
        "name": "New Admin",
        "email": "newadmin@test.com",
        "password": "Password123!",
        "role": "admin"
    })
    assert res.status_code == 401


def test_admin_create_user_by_loan_officer_forbidden(phase2b_db):
    """Loan officers cannot create new users or promote users via admin workflow."""
    session = phase2b_db["SessionLocal"]()
    officer = User(
        name="Loan Officer",
        email="loanofficer@test.com",
        password="hashed_pass",
        role=UserRole.loan_officer,
    )
    session.add(officer)
    session.commit()
    session.refresh(officer)

    token = create_access_token({"sub": str(officer.id), "role": "loan_officer"})
    client = phase2b_db["client"]

    res = client.post(
        "/auth/admin/create-user",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Escalated User",
            "email": "escalated@test.com",
            "password": "Password123!",
            "role": "admin"
        }
    )
    assert res.status_code == 403
    session.close()


def test_admin_create_user_by_admin_success(phase2b_db):
    """Authenticated administrators can successfully create new admins and officers."""
    session = phase2b_db["SessionLocal"]()
    admin = User(
        name="Chief Admin",
        email="chief@test.com",
        password="hashed_pass",
        role=UserRole.admin,
    )
    session.add(admin)
    session.commit()
    session.refresh(admin)

    token = create_access_token({"sub": str(admin.id), "role": "admin"})
    client = phase2b_db["client"]

    # 1. Admin creates another Admin
    res = client.post(
        "/auth/admin/create-user",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Second Admin",
            "email": "second_admin@test.com",
            "password": "Password123!",
            "role": "admin"
        }
    )
    assert res.status_code == 201
    assert res.json()["role"] == "admin"

    # 2. Admin creates a Loan Officer
    res2 = client.post(
        "/auth/admin/create-user",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Third Officer",
            "email": "third_officer@test.com",
            "password": "Password123!",
            "role": "loan_officer"
        }
    )
    assert res2.status_code == 201
    assert res2.json()["role"] == "loan_officer"
    session.close()


# ─────────────────────────────────────────────────────────────────────────────
# Fix 3: PostgreSQL URL Normalization
# ─────────────────────────────────────────────────────────────────────────────

def test_normalize_database_url_render_postgres():
    """Render-style postgres:// URLs are normalized to postgresql://."""
    url = "postgres://smartverify_user:Secr3t_p@ss@dpg-c0123-a.oregon-postgres.render.com/smartverify"
    norm = normalize_database_url(url)
    assert norm.startswith("postgresql://")
    assert norm == "postgresql://smartverify_user:Secr3t_p@ss@dpg-c0123-a.oregon-postgres.render.com/smartverify"


def test_normalize_database_url_already_normalized():
    """Already normalized postgresql:// URLs are not modified or double-prefixed."""
    url = "postgresql://user:pass@host:5432/db"
    assert normalize_database_url(url) == url


def test_normalize_database_url_sqlite():
    """SQLite URLs remain intact."""
    sqlite_rel = "sqlite:///./smartverify.db"
    sqlite_mem = "sqlite:///:memory:"
    assert normalize_database_url(sqlite_rel) == sqlite_rel
    assert normalize_database_url(sqlite_mem) == sqlite_mem
    assert normalize_database_url("") == ""


def test_alembic_offline_postgres_sql_generation():
    """Verify Alembic successfully generates PostgreSQL migration DDL with normalized URL."""
    cfg = alembic.config.Config("alembic.ini")
    # Simulate Render passing a postgres:// URL to Alembic
    raw_render_url = "postgres://render_user:mock_password@localhost:5432/render_db"
    norm_url = normalize_database_url(raw_render_url)
    cfg.set_main_option("sqlalchemy.url", norm_url)

    buf = io.StringIO()
    with redirect_stdout(buf):
        command.upgrade(cfg, "head", sql=True)

    sql_output = buf.getvalue()
    assert "CREATE TABLE users" in sql_output
    assert "CREATE TABLE reverification_reports" in sql_output
    assert "alembic_version" in sql_output

