"""Verification pipeline endpoints.

Exposes two verification strategies sharing the same underlying
services and the same VerificationReport storage:

    POST /verify/{application_id}           – deterministic rule-based pipeline
    POST /verify/{application_id}/agentic    – CrewAI multi-agent pipeline
"""
import logging
import concurrent.futures
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.application import Application, ApplicationStatus
from app.models.document import Document
from app.models.verification_report import VerificationReport
from app.models.user import User
from app.schemas.report import ReportOut
from app.core.security import get_current_user
from app.core.config import settings
from app.services.nlp_service import extract_information
from app.services.verification_engine import VerificationEngine
from app.services.fraud_detection import FraudDetector
from app.services.verification_normalizer import normalize_and_persist_verification_findings
from app.api.endpoints.documents import authorize_application_access

router = APIRouter()
logger = logging.getLogger(__name__)

# Process-level thread-safe lock and worker tracking for deduplicating concurrent verification requests.
_active_verifications_lock = threading.Lock()
_active_verifications: set[int] = set()


class ActiveVerificationTracker:
    """
    Thread-safe tracker managing in-progress verification workers.
    Ensures:
    1. Lock retention until background worker thread actually halts.
    2. Setting abort event on timeout.
    3. Rejection of duplicate requests with 409 while worker remains active.
    4. Safe cleanup only from worker's termination path.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._workers: Dict[int, Dict[str, Any]] = {}

    def try_acquire(self, application_id: int) -> Tuple[bool, Optional[str], threading.Event]:
        with self._lock:
            if application_id in self._workers:
                worker_info = self._workers[application_id]
                future = worker_info.get("future")
                thread = worker_info.get("thread")
                is_running = True
                if future is not None:
                    is_running = future.running() or not future.done()
                elif thread is not None:
                    is_running = thread.is_alive()

                if is_running:
                    if worker_info.get("timed_out", False):
                        reason = (
                            f"Verification workflow for application #{application_id} "
                            f"timed out but background worker is still active. Duplicate execution rejected."
                        )
                    else:
                        reason = (
                            f"Verification workflow is already active for application #{application_id}. "
                            f"Duplicate execution rejected."
                        )
                    return False, reason, worker_info["abort_event"]
                else:
                    del self._workers[application_id]

            abort_event = threading.Event()
            self._workers[application_id] = {
                "abort_event": abort_event,
                "future": None,
                "thread": None,
                "timed_out": False,
                "start_time": datetime.now(timezone.utc),
            }
            return True, None, abort_event

    def register_future(self, application_id: int, future: Any):
        with self._lock:
            if application_id in self._workers:
                self._workers[application_id]["future"] = future

    def register_thread(self, application_id: int, thread: threading.Thread):
        with self._lock:
            if application_id in self._workers:
                self._workers[application_id]["thread"] = thread

    def mark_timed_out(self, application_id: int):
        with self._lock:
            if application_id in self._workers:
                self._workers[application_id]["timed_out"] = True
                self._workers[application_id]["abort_event"].set()

    def is_timed_out(self, application_id: int) -> bool:
        with self._lock:
            if application_id in self._workers:
                return self._workers[application_id].get("timed_out", False)
            return False

    def is_active(self, application_id: int) -> bool:
        with self._lock:
            if application_id not in self._workers:
                return False
            worker_info = self._workers[application_id]
            future = worker_info.get("future")
            thread = worker_info.get("thread")
            if future is not None:
                return future.running() or not future.done()
            if thread is not None:
                return thread.is_alive()
            return True

    def release_worker(self, application_id: int):
        with self._lock:
            if application_id in self._workers:
                del self._workers[application_id]


_verification_tracker = ActiveVerificationTracker()
_worker_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=8,
    thread_name_prefix="smartverify-worker",
)


def _load_application_and_documents(application_id: int, db: Session, current_user: Optional[User] = None):
    app = db.query(Application).filter(Application.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    if current_user is not None:
        authorize_application_access(app, current_user)

    docs = db.query(Document).filter(Document.application_id == application_id).all()
    if not docs:
        raise HTTPException(status_code=400, detail="No documents uploaded for this application")

    return app, docs


def _get_or_create_report(application_id: int, db: Session) -> VerificationReport:
    report = db.query(VerificationReport).filter(
        VerificationReport.application_id == application_id
    ).first()
    if not report:
        report = VerificationReport(application_id=application_id)
        db.add(report)
    return report


@router.post("/{application_id}", response_model=ReportOut)
def run_verification(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Deterministic rule-based verification pipeline:
    1. Aggregate OCR text from all documents
    2. NLP extraction
    3. Verification engine
    4. Fraud detection
    5. PDF report generation
    """
    app, docs = _load_application_and_documents(application_id, db, current_user=current_user)

    # ── 1. Aggregate all OCR text ─────────────────────────────────────
    combined_text = " ".join(d.extracted_text or "" for d in docs)

    # ── 2. NLP extraction ─────────────────────────────────────────────
    extracted_info = extract_information(combined_text)
    if not extracted_info.get("loan_amount"):
        extracted_info["loan_amount"] = app.loan_amount
    if not extracted_info.get("applicant_name") and app.applicant_name:
        extracted_info["applicant_name"] = app.applicant_name

    # ── 3. Verification engine ────────────────────────────────────────
    doc_dicts = [{"document_type": d.document_type, "id": d.id} for d in docs]
    engine = VerificationEngine()
    verification_result = engine.verify(extracted_info, doc_dicts)

    # ── 4. Fraud detection ────────────────────────────────────────────
    detector = FraudDetector()
    fraud_result = detector.analyse(extracted_info, doc_dicts, application_id, db)

    # ── 5. Determine final status ─────────────────────────────────────
    final_status = verification_result["status"]
    if fraud_result["fraud_flag"]:
        final_status = "rejected"

    # ── 6. Update application status ─────────────────────────────────
    app.status = final_status
    if extracted_info.get("applicant_name") and not app.applicant_name:
        app.applicant_name = extracted_info["applicant_name"]
    db.commit()

    # ── 7. Normalize findings bridge ──────────────────────────────────
    normalize_and_persist_verification_findings(
        application_id=application_id,
        db=db,
        extracted_info=extracted_info,
        verification_details=verification_result,
        fraud_analysis=fraud_result,
        agent_trace=None,
        documents=docs,
    )
    pdf_path = None

    # ── 8. Save / update report record ───────────────────────────────
    report = _get_or_create_report(application_id, db)
    report.verification_score   = verification_result["verification_score"]
    report.risk_score           = fraud_result["risk_score"]
    report.fraud_flag           = fraud_result["fraud_flag"]
    report.status               = final_status
    report.extracted_info       = extracted_info
    report.fraud_analysis       = fraud_result
    report.verification_details = verification_result
    report.pdf_path             = pdf_path
    report.verification_mode    = "rule_based"
    report.agent_summary        = None
    report.agent_trace          = None
    db.commit()
    db.refresh(report)

    logger.info(f"[rule_based] Verification complete for app {application_id}: {final_status}")
    return report


@router.post("/{application_id}/agentic", response_model=ReportOut)
def run_agentic_verification(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Multi-agent (CrewAI) verification pipeline.

    A crew of five specialised agents collaborates to read documents,
    extract applicant information, apply verification rules, assess
    fraud risk, and compile the final PDF report:

        Document Analyst → Data Extraction Specialist
            → Loan Verification Officer
            → Fraud Investigator
                → Compliance Reporter

    The underlying tools call the exact same deterministic services as
    POST /verify/{application_id}, so results are explainable and
    consistent — the agents add reasoning, cross-checking, and a
    natural-language summary on top.
    """
    app, docs = _load_application_and_documents(application_id, db, current_user=current_user)

    if not settings.CREWAI_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Agentic verification is disabled (CREWAI_ENABLED=false).",
        )

    # ── Concurrency & Duplicate Request Protection ───────────────────
    with _active_verifications_lock:
        if application_id in _active_verifications or _verification_tracker.is_active(application_id):
            if _verification_tracker.is_timed_out(application_id):
                raise HTTPException(
                    status_code=409,
                    detail=f"Verification workflow for application #{application_id} timed out but background worker is still active. Duplicate execution rejected."
                )
            raise HTTPException(
                status_code=409,
                detail=f"Verification workflow is already active for application #{application_id}. Duplicate execution rejected."
            )

        # Cross-process check: Verify if DB marks this application as in_progress
        if app.status == ApplicationStatus.in_progress or str(app.status) == "in_progress":
            now = datetime.utcnow()
            stale_threshold = getattr(settings, "WORKFLOW_TIMEOUT_SECONDS", 300.0) + 30.0
            if app.updated_at and (now - app.updated_at).total_seconds() < stale_threshold:
                raise HTTPException(
                    status_code=409,
                    detail=f"Verification workflow is already in progress for application #{application_id} (process lock). Duplicate execution rejected."
                )
            else:
                logger.warning(f"Overriding stale in_progress lock for application #{application_id} (last updated {app.updated_at})")

        acquired, reason, abort_event = _verification_tracker.try_acquire(application_id)
        if not acquired:
            raise HTTPException(status_code=409, detail=reason)
        _active_verifications.add(application_id)

    # Mark application as in_progress immediately in the database
    app.status = ApplicationStatus.in_progress
    db.commit()

    try:
        doc_payload = [
            {
                "id": d.id,
                "document_type": d.document_type.value if hasattr(d.document_type, "value") else d.document_type,
                "file_path": d.file_path,
                "original_name": d.original_name,
                "structured_data": d.structured_data,
                "extracted_text": d.extracted_text,
            }
            for d in docs
        ]

        from app.agents.crew import LoanVerificationCrew
        from app.models.application import GovVerification
        gov_ver = db.query(GovVerification).filter(GovVerification.application_id == application_id).first()
        gov_record = {}
        if gov_ver:
            gov_record = {
                # Canonical model fields
                "pan_aadhaar_link_status": gov_ver.pan_aadhaar_link_status,
                "aadhaar_validity_status": gov_ver.aadhaar_validity_status,
                "tax_receipt_status": gov_ver.tax_receipt_status,
                "aadhaar_screenshot_path": gov_ver.aadhaar_screenshot_path,
                "screenshot_path": gov_ver.screenshot_path,
                "verification_screenshots": gov_ver.verification_screenshots,
                "officer_name": gov_ver.officer_name,
                "timestamp": gov_ver.timestamp,
                "remarks": gov_ver.remarks,
                # Backward compatibility aliases mapped to current model fields
                "aadhaar_status": gov_ver.aadhaar_validity_status,
                "pan_status": gov_ver.pan_aadhaar_link_status,
                "pan_aadhaar_status": gov_ver.pan_aadhaar_link_status,
                "tax_status": gov_ver.tax_receipt_status,
                "tax_receipt": gov_ver.tax_receipt_status,
                "screenshot_uploaded": bool(
                    gov_ver.aadhaar_screenshot_path
                    or gov_ver.screenshot_path
                    or gov_ver.verification_screenshots
                ),
            }

        app_applicant_name = app.applicant_name or ""
        app_loan_amount = app.loan_amount
        app_loan_type = app.loan_type or "Home Loan"
        app_loan_tenure = app.loan_tenure or 60

        def _worker_fn():
            from app.db.database import SessionLocal
            worker_db = SessionLocal()
            try:
                crew = LoanVerificationCrew(db_session=worker_db)
                res = crew.run(
                    application_id=application_id,
                    applicant_name=app_applicant_name,
                    loan_amount=app_loan_amount,
                    loan_type=app_loan_type,
                    loan_tenure=app_loan_tenure,
                    documents=doc_payload,
                    gov_verification_record=gov_record,
                    abort_event=abort_event,
                )
                if _verification_tracker.is_timed_out(application_id):
                    logger.warning(
                        f"[Pipeline Timing] App #{application_id}: Worker completed after overall timeout. "
                        f"Discarding late result and suppressing report persistence."
                    )
                    return None
                return res
            except Exception as worker_exc:
                import traceback
                from app.services.llm_provider import mask_secrets
                sanitized_err = mask_secrets(str(worker_exc))
                tb = mask_secrets(traceback.format_exc())
                logger.error(
                    f"[Worker Exception] App #{application_id}: Background worker failed with "
                    f"{type(worker_exc).__name__}: {sanitized_err}\nTraceback:\n{tb}"
                )
                raise
            finally:
                try:
                    worker_db.close()
                except Exception:
                    pass
                _verification_tracker.release_worker(application_id)
                with _active_verifications_lock:
                    _active_verifications.discard(application_id)
                logger.info(f"App #{application_id}: Background worker terminated and lock released.")

        future = _worker_executor.submit(_worker_fn)
        _verification_tracker.register_future(application_id, future)
    except Exception:
        _verification_tracker.release_worker(application_id)
        with _active_verifications_lock:
            _active_verifications.discard(application_id)
        raise

    workflow_timeout = getattr(settings, "WORKFLOW_TIMEOUT_SECONDS", 300.0)

    try:
        result = future.result(timeout=workflow_timeout)
    except concurrent.futures.TimeoutError:
        _verification_tracker.mark_timed_out(application_id)
        logger.error(
            f"[LLM Timing] App #{application_id}: Verification workflow timed out after {workflow_timeout}s. "
            f"Abort event set. In-progress lock retained until worker thread halts."
        )
        try:
            app.status = ApplicationStatus.failed
            db.commit()
        except Exception as db_err:
            logger.error(f"Failed to record failed status in DB for app #{application_id}: {db_err}")

        raise HTTPException(
            status_code=504,
            detail=f"Verification workflow timed out after {workflow_timeout}s. Background worker is aborting. Please retry once worker halts.",
        )
    except Exception as e:
        _verification_tracker.mark_timed_out(application_id)
        try:
            app.status = ApplicationStatus.failed
            db.commit()
        except Exception as db_err:
            logger.error(f"Failed to record failed status in DB for app #{application_id}: {db_err}")

        from app.services.llm_provider import mask_secrets
        sanitized_detail = mask_secrets(str(e))
        logger.error(f"Agentic verification crew failed for app {application_id}: {sanitized_detail}")
        raise HTTPException(status_code=500, detail=f"Agentic verification failed: {sanitized_detail}")

    if result is None or _verification_tracker.is_timed_out(application_id):
        raise HTTPException(
            status_code=504,
            detail=f"Verification workflow timed out after {workflow_timeout}s. Late result discarded.",
        )

    final_status        = result["final_status"]
    extracted_info       = result.get("extracted_info") or {}
    verification_result = result.get("verification_details") or {}
    fraud_result         = result.get("fraud_analysis") or {}
    pdf_path             = result.get("pdf_path")

    # ── Update application status ─────────────────────────────────────
    app.status = final_status
    if extracted_info.get("applicant_name") and not app.applicant_name:
        app.applicant_name = extracted_info["applicant_name"]
    db.commit()

    # ── Decouple from legacy PDF generator ────────────────────────────
    # Downstream report review and publication PDF are generated on-demand
    pdf_path = None

    # ── Save / update report record ───────────────────────────────────
    report = _get_or_create_report(application_id, db)
    report.verification_score   = result.get("verification_score", 0.0)
    report.risk_score           = result.get("risk_score", 0.0)
    report.fraud_flag           = result.get("fraud_flag", False)
    report.status               = final_status
    report.extracted_info       = extracted_info
    report.fraud_analysis       = fraud_result
    report.verification_details = verification_result
    report.pdf_path             = pdf_path
    report.verification_mode    = "agentic"
    report.agent_summary        = result.get("summary")
    report.agent_trace          = {
        "raw": result.get("agent_trace"),
        "agent_findings": result.get("agent_findings", {}),
        "recommendation": result.get("recommendation", ""),
        "human_review": result.get("human_review", ""),
    }
    
    # New Multi-Agent Fields (Phases 6, 7, 8)
    report.confidence_score     = result.get("confidence_score", 0.0)
    report.explainable_ai       = result.get("explainable_ai", {})
    report.agent_memory         = result.get("agent_memory", [])
    report.execution_timeline   = result.get("execution_timeline", {})
    
    db.commit()
    db.refresh(report)

    # ── Normalize findings bridge ─────────────────────────────────────
    normalize_and_persist_verification_findings(
        application_id=application_id,
        db=db,
        extracted_info=extracted_info,
        verification_details=verification_result,
        fraud_analysis=fraud_result,
        agent_trace=report.agent_trace,
        documents=docs,
    )

    # ── Store Application in Vector Database for Similarity Search (Phase 3) ──
    try:
        from app.services.vector_service import VectorService
        VectorService().store_application_vector(
            app_id=application_id,
            extracted_info=extracted_info,
            fraud_result=fraud_result,
            verification_score=report.verification_score,
            status=final_status
        )
    except Exception as e:
        logger.error(f"Failed to index application for similarity search: {e}")

    logger.info(f"[agentic] Verification complete for app {application_id}: {final_status}")
    return report


@router.get("/{application_id}/status")
def get_verification_status(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Check the live status of an agentic verification job.
    Supports frontend progress display and safe polling up to 5 minutes.
    """
    app = db.query(Application).filter(Application.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    authorize_application_access(app, current_user, db)

    is_active = _verification_tracker.is_active(application_id)
    is_timed_out = _verification_tracker.is_timed_out(application_id)

    # Check if report already exists in DB
    report = db.query(VerificationReport).filter(VerificationReport.application_id == application_id).first()
    report_data = None
    if report and app.status not in (ApplicationStatus.in_progress, "in_progress"):
        from app.schemas.report import ReportOut
        report_data = ReportOut.model_validate(report).model_dump()

    return {
        "application_id": application_id,
        "status": app.status.value if hasattr(app.status, "value") else str(app.status),
        "is_active": is_active,
        "is_timed_out": is_timed_out,
        "report": report_data,
        "applicant_name": app.applicant_name,
        "loan_amount": app.loan_amount,
    }
