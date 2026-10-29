"""
SmartVerify - Autonomous AI System for Intelligent Loan Verification
Main FastAPI Application Entry Point
"""
import logging, os
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.logging_config import setup_logging
from app.api.endpoints import auth, documents, verification, applications, dashboard, reports, gov_screenshots
from app.db.database import engine, Base
from fastapi import Depends, HTTPException, Response
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.application import Application
from app.models.document import Document
from app.models.government_screenshot import GovernmentVerificationScreenshot
from app.api.endpoints.documents import authorize_application_access
from app.services.storage_service import get_storage_service


setup_logging()
logger = logging.getLogger(__name__)

# Create all tables only for SQLite local development; PostgreSQL schema is managed by Alembic
if settings.DATABASE_URL.startswith("sqlite"):
    Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="SmartVerify API",
    description="Autonomous AI System for Intelligent Loan Verification",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_origin_regex=r"https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
os.makedirs(settings.REPORT_DIR, exist_ok=True)
# Unauthenticated static uploads mount removed for security

# API Routers
app.include_router(auth.router,         prefix="/auth",         tags=["Authentication"])
app.include_router(auth.router,         prefix="/api/auth",     tags=["Authentication"])
app.include_router(documents.router,    prefix="/documents",    tags=["Documents"])
app.include_router(documents.router,    prefix="/api/documents",tags=["Documents"])
app.include_router(verification.router, prefix="/verify",       tags=["Verification"])
app.include_router(verification.router, prefix="/api/verify",   tags=["Verification"])
app.include_router(applications.router, prefix="/applications", tags=["Applications"])
app.include_router(applications.router, prefix="/api/applications", tags=["Applications"])
app.include_router(dashboard.router,    prefix="/dashboard",    tags=["Dashboard"])
app.include_router(dashboard.router,    prefix="/api/dashboard",tags=["Dashboard"])
app.include_router(reports.router,      prefix="/report",       tags=["Reports"])
app.include_router(reports.router,      prefix="/api/reports",  tags=["Reports"])
app.include_router(gov_screenshots.router, prefix="/gov-screenshots", tags=["Government Screenshots"])
app.include_router(gov_screenshots.router, prefix="/api/gov-screenshots", tags=["Government Screenshots"])

@app.get("/uploads/{filename:path}", include_in_schema=False)
@app.get("/api/uploads/{filename:path}", include_in_schema=False)
def get_secure_upload_file(
    filename: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Secure retrieval of applicant upload files.
    Enforces authentication and application authorization before returning any file.
    Unauthenticated or unauthorized access is denied.
    """
    clean_filename = os.path.basename(filename.replace("\\", "/"))
    if not clean_filename or clean_filename != filename or ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    doc = (
        db.query(Document)
        .filter(Document.file_path.like(f"%{clean_filename}"))
        .first()
    )
    shot = None
    if not doc:
        shot = (
            db.query(GovernmentVerificationScreenshot)
            .filter(GovernmentVerificationScreenshot.screenshot_path.like(f"%{clean_filename}"))
            .first()
        )

    if not doc and not shot:
        raise HTTPException(status_code=404, detail="File not found")

    target_app_id = doc.application_id if doc else shot.application_id
    app_record = db.query(Application).filter(Application.id == target_app_id).first()
    if not app_record:
        raise HTTPException(status_code=404, detail="Application not found")

    authorize_application_access(app_record, current_user)

    storage = get_storage_service()
    if settings.STORAGE_PROVIDER == "supabase" and doc and doc.file_path:
        try:
            data = storage.download_file(settings.SUPABASE_DOCUMENTS_BUCKET, doc.file_path)
            ext = os.path.splitext(clean_filename)[1].lower()
            media_type = "application/pdf" if ext == ".pdf" else f"image/{ext.lstrip('.') or 'png'}"
            return Response(content=data, media_type=media_type)
        except Exception as e:
            logger.error(f"Failed to retrieve file from Supabase storage: {e}")
            raise HTTPException(status_code=500, detail="Failed to retrieve file from storage provider")

    disk_path = (doc.file_path if doc else shot.screenshot_path) or ""
    if not os.path.exists(disk_path):
        fallback = os.path.join(settings.UPLOAD_DIR, clean_filename)
        if os.path.exists(fallback):
            disk_path = fallback
        else:
            raise HTTPException(status_code=404, detail="File not found on disk")

    ext = os.path.splitext(disk_path)[1].lower()
    media_type = "application/pdf" if ext == ".pdf" else f"image/{ext.lstrip('.') or 'png'}"
    return FileResponse(disk_path, media_type=media_type)



@app.on_event("startup")
def ensure_default_users():
    """
    Controlled administrative bootstrap & optional demo user seeding.
    Production startup will NEVER seed hardcoded demo credentials unless
    SEED_DEMO_USERS=true is explicitly configured.
    An initial administrator can be safely bootstrapped using INITIAL_ADMIN_EMAIL
    and INITIAL_ADMIN_PASSWORD environment variables without logging passwords.
    Existing users in the database are never locked out or overwritten.
    """
    from sqlalchemy import func
    from app.db.database import SessionLocal
    from app.models.user import User, UserRole
    from app.core.security import hash_password
    db = SessionLocal()
    try:
        # 1. Safe One-Time Initial Administrator Bootstrap (if configured)
        init_email = (getattr(settings, "INITIAL_ADMIN_EMAIL", "") or "").strip().lower()
        init_password = getattr(settings, "INITIAL_ADMIN_PASSWORD", "") or ""
        if init_email and init_password:
            existing_admin = db.query(User).filter(func.lower(User.email) == init_email).first()
            if not existing_admin:
                admin = User(
                    name="System Administrator",
                    email=init_email,
                    password=hash_password(init_password),
                    role=UserRole.admin,
                )
                db.add(admin)
                logger.info("Successfully bootstrapped initial administrator account: %s", init_email)
            else:
                logger.debug("Initial administrator account %s already exists.", init_email)

        # 2. Opt-in Demo User Seeding (Development / Staging Only, default False)
        if getattr(settings, "SEED_DEMO_USERS", False):
            admin_email = "admin@smartverify.com"
            admin = db.query(User).filter(func.lower(User.email) == admin_email).first()
            if not admin:
                admin = User(
                    name="Demo Admin User",
                    email=admin_email,
                    password=hash_password("admin123"),
                    role=UserRole.admin,
                )
                db.add(admin)
                logger.info("Demo user seeding: created demo admin (%s)", admin_email)

            officer_email = "officer@smartverify.com"
            officer = db.query(User).filter(func.lower(User.email) == officer_email).first()
            if not officer:
                officer = User(
                    name="Demo Loan Officer",
                    email=officer_email,
                    password=hash_password("officer123"),
                    role=UserRole.loan_officer,
                )
                db.add(officer)
                logger.info("Demo user seeding: created demo officer (%s)", officer_email)

        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("Could not verify/seed default users: %s", e)
    finally:
        db.close()


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}


# ── Frontend SPA Integration ──────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_BUILD_DIR = Path(
    os.getenv("FRONTEND_BUILD_DIR", str(BASE_DIR.parent / "frontend" / "build"))
)

if FRONTEND_BUILD_DIR.is_dir() and (FRONTEND_BUILD_DIR / "index.html").is_file():
    logger.info("Serving frontend SPA from %s", FRONTEND_BUILD_DIR)

    # Mount static assets (CSS, JS, media generated by CRA)
    frontend_static = FRONTEND_BUILD_DIR / "static"
    if frontend_static.is_dir():
        app.mount("/static", StaticFiles(directory=str(frontend_static)), name="frontend_static")

    @app.middleware("http")
    async def spa_middleware(request: Request, call_next):
        path = request.url.path

        # Preserve backend API, documentation, static uploads/reports, and file downloads
        is_api_or_system = (
            path.startswith("/api/")
            or path.startswith("/auth/")
            or path == "/auth"
            or path.startswith("/documents/")
            or path == "/documents"
            or path.startswith("/gov-screenshots/")
            or path == "/gov-screenshots"
            or path.startswith("/verify/")
            or path == "/verify"
            or path.startswith("/applications/")
            or path == "/applications"
            or path.startswith("/dashboard/stats")
            or path.startswith("/report/")
            or path == "/report"
            or path.startswith("/openapi.json")
            or path.startswith("/docs")
            or path.startswith("/redoc")
            or path == "/health"
            or path.startswith("/uploads/")
            or path.startswith("/reports/")
            or path.startswith("/static/")
            or path.endswith("/download")
        )

        # If a browser is requesting a page directly (Accept header includes text/html),
        # return index.html for client-side routing, avoiding JSON/401 API responses
        if request.method in ("GET", "HEAD") and not is_api_or_system:
            accept = request.headers.get("accept", "")
            if "text/html" in accept:
                file_target = FRONTEND_BUILD_DIR / path.lstrip("/")
                if file_target.is_file() and path != "/":
                    return FileResponse(str(file_target))
                return FileResponse(str(FRONTEND_BUILD_DIR / "index.html"))

        response = await call_next(request)

        # Fallback to index.html for unknown GET paths (client-side SPA routing)
        if response.status_code == 404 and request.method in ("GET", "HEAD") and not is_api_or_system:
            file_target = FRONTEND_BUILD_DIR / path.lstrip("/")
            if file_target.is_file():
                return FileResponse(str(file_target))
            return FileResponse(str(FRONTEND_BUILD_DIR / "index.html"))

        return response

    @app.get("/", tags=["Frontend"])
    async def root():
        return FileResponse(str(FRONTEND_BUILD_DIR / "index.html"))

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_catch_all(full_path: str):
        norm_path = "/" + full_path.lstrip("/")
        if (
            norm_path.startswith("/api/")
            or norm_path.startswith("/auth/")
            or norm_path.startswith("/documents/")
            or norm_path.startswith("/gov-screenshots/")
            or norm_path.startswith("/verify/")
            or norm_path.startswith("/applications/")
            or norm_path.startswith("/dashboard/")
            or norm_path.startswith("/report/")
            or norm_path.startswith("/uploads/")
            or norm_path.startswith("/reports/")
            or norm_path.endswith("/download")
        ):
            raise HTTPException(status_code=404, detail="Not Found")
        file_target = FRONTEND_BUILD_DIR / full_path
        if file_target.is_file():
            return FileResponse(str(file_target))
        return FileResponse(str(FRONTEND_BUILD_DIR / "index.html"))

else:
    logger.info("No frontend build found at %s. API-only mode.", FRONTEND_BUILD_DIR)

    @app.get("/", tags=["Health"])
    async def root():
        return {"status": "ok", "service": "SmartVerify API", "version": "1.0.0"}

