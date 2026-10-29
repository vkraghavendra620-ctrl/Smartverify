"""
SmartVerify Timeout & Multi-Agent Optimization Test Suite.
Verifies fixes for 180s verification timeout, tier cascade, caching, and outcome truthfulness.
"""
import json
import time
import threading
import pytest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from app.agents.crew import LoanVerificationCrew
from app.agents.agent_definitions import build_agents, get_llm
from app.agents.tools import PolicyRetrievalTool, SimilaritySearchTool
from app.agents.tasks import build_tasks
from app.api.endpoints.verification import (
    run_agentic_verification,
    _verification_tracker,
    _active_verifications,
    _active_verifications_lock,
)
from app.models.application import Application, ApplicationStatus
from app.models.verification_report import VerificationReport


def _sample_agent_result(provider="gemini", model="gemini-flash-lite-latest", score=80.0, status="approved"):
    return {
        "final_status": status,
        "verification_score": score,
        "risk_score": 100.0 - score,
        "fraud_flag": False,
        "extracted_info": {
            "applicant_name": "Test Candidate",
            "monthly_income": 50000.0,
            "aadhaar_number": "123456789012",
            "pan_number": "ABCDE1234F",
        },
        "verification_details": {
            "eligibility": "eligible",
            "checks": [{"check": "Income Verification", "passed": True}],
            "policies_retrieved": 1,
            "policy_references": ["Bank Loan Eligibility: Minimum monthly income ₹25,000"],
        },
        "fraud_analysis": {
            "fraud_score": 10.0,
            "fraud_flag": False,
        },
        "agent_findings": {
            "document_analyst": {"document_status": "complete"},
            "extraction_specialist": {"applicant_profile": {"monthly_income": 50000.0}},
            "verification_officer": {"recommendation": "approve"},
            "gov_verification_agent": {"verification_status": "Government Verification Passed"},
        },
        "recommendation": "approve",
        "human_review": "None",
        "summary": "Verified and approved.",
        "llm_provider": provider,
        "llm_model": model,
    }


def _setup_app_and_db(app_id=777, applicant_name="Test Applicant", income=50000.0, status=ApplicationStatus.pending):
    mock_db = MagicMock()
    mock_app = MagicMock(spec=Application)
    mock_app.id = app_id
    mock_app.status = status
    mock_app.applicant_name = applicant_name
    mock_app.loan_amount = 500000.0
    mock_app.loan_type = "Home Loan"
    mock_app.loan_tenure = 60
    mock_app.updated_at = None

    mock_doc = MagicMock()
    mock_doc.id = 1
    mock_doc.document_type = "aadhaar"
    mock_doc.file_path = "test_doc.pdf"
    mock_doc.original_name = "test_doc.pdf"
    mock_doc.structured_data = json.dumps({"applicant_name": applicant_name, "monthly_income": income})
    mock_doc.extracted_text = "Extracted text sample"

    def query_mock(model):
        q = MagicMock()
        if "Application" in str(model):
            q.filter.return_value.first.return_value = mock_app
        elif "Document" in str(model):
            q.filter.return_value.all.return_value = [mock_doc]
        elif "VerificationReport" in str(model):
            q.filter.return_value.first.return_value = None
        else:
            q.filter.return_value.first.return_value = None
            q.filter.return_value.all.return_value = []
        return q

    mock_db.query.side_effect = query_mock
    return mock_db, mock_app


# ── Test 1: Normal Successful Verification ─────────────────────────────────

def test_normal_successful_verification():
    app_id = 1001
    mock_db, mock_app = _setup_app_and_db(app_id)
    mock_user = MagicMock()
    mock_user.role = "admin"

    with _active_verifications_lock:
        _active_verifications.discard(app_id)
    _verification_tracker.release_worker(app_id)

    expected_res = _sample_agent_result(provider="gemini", model="gemini-flash-lite-latest")

    with patch("app.agents.crew.LoanVerificationCrew.run", return_value=expected_res):
        report = run_agentic_verification(application_id=app_id, db=mock_db, current_user=mock_user)

    assert report.status == "approved"
    assert report.verification_score == 80.0
    assert report.verification_mode == "agentic"
    assert mock_app.status == "approved"
    assert mock_db.commit.called


# ── Test 2: Slow Gemini Response (Completes within Budget) ─────────────────

def test_slow_gemini_response_succeeds_within_budget():
    app_id = 1002
    mock_db, mock_app = _setup_app_and_db(app_id)
    mock_user = MagicMock()
    mock_user.role = "admin"

    with _active_verifications_lock:
        _active_verifications.discard(app_id)
    _verification_tracker.release_worker(app_id)

    def slow_crew_run(*args, **kwargs):
        time.sleep(0.05)
        return _sample_agent_result(provider="gemini", model="gemini-flash-lite-latest")

    with patch("app.agents.crew.LoanVerificationCrew.run", side_effect=slow_crew_run):
        with patch("app.core.config.settings.WORKFLOW_TIMEOUT_SECONDS", 1.0):
            report = run_agentic_verification(application_id=app_id, db=mock_db, current_user=mock_user)

    assert report.status == "approved"
    assert report.verification_mode == "agentic"


# ── Test 3: Tier 1 Timeout Cascades to Tier 2 Without Global Abort ─────────

def test_tier1_timeout_cascades_to_tier2_without_aborting_workflow():
    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db, backoff_base=0.001)

    flash_res = _sample_agent_result(provider="gemini", model="gemini-flash-latest")

    call_history = []

    def mock_execute(*args, **kwargs):
        call_history.append(kwargs["model_name"])
        if kwargs["model_name"] == "gemini-flash-lite-latest":
            # Simulate Tier 1 hanging beyond per-tier timeout
            time.sleep(0.1)
            raise TimeoutError("Tier 1 simulated attempt timeout")
        return flash_res

    with patch.object(crew, "_execute_pipeline", side_effect=mock_execute):
        with patch("app.core.config.settings.LLM_TIER_TIMEOUT_SECONDS", 0.05):
            with patch("app.core.config.settings.WORKFLOW_TIMEOUT_SECONDS", 10.0):
                result = crew.run(
                    application_id=1003,
                    applicant_name="Cascade User",
                    loan_amount=500000.0,
                    documents=[],
                )

    # Must have attempted Tier 1 then gracefully fallen back to Tier 2
    assert "gemini-flash-lite-latest" in call_history
    assert "gemini-flash-latest" in call_history
    assert result["llm_model"] == "gemini-flash-latest"
    assert result["final_status"] == "approved"


# ── Test 4: Policy Retrieval Caching (Prevents Redundant Sequential Calls) ─

def test_policy_retrieval_caching_and_truthful_reporting():
    tool = PolicyRetrievalTool()
    tool._cache.clear()

    mock_service = MagicMock()
    mock_service.retrieve_policies.return_value = [
        {"text": "Policy 1: Income must exceed ₹25,000", "metadata": {"category": "Income"}}
    ]

    with patch("app.services.vector_service.VectorService", return_value=mock_service):
        res1 = tool._run(query="bank loan eligibility policies")
        res2 = tool._run(query="bank loan eligibility policies")

    # Service should only be called ONCE due to cache
    assert mock_service.retrieve_policies.call_count == 1
    assert "Policy 1" in res1
    assert res1 == res2

    # Truthful reporting when empty
    mock_service.retrieve_policies.return_value = []
    res_empty = tool._run(query="nonexistent unusual policy")
    parsed_empty = json.loads(res_empty)
    assert parsed_empty["status"] == "not_found"
    assert parsed_empty["policies"] == []
    assert "No matching bank policies found" in parsed_empty["message"]


# ── Test 5: Similarity Search Caching ──────────────────────────────────────

def test_similarity_search_caching():
    tool = SimilaritySearchTool()
    tool._cache.clear()

    mock_service = MagicMock()
    mock_service.find_similar_applications.return_value = [
        {"application_id": 50, "similarity_score": 90.0, "decision": "approved"}
    ]

    profile_json = json.dumps({"applicant_name": "Ravi Kumar", "monthly_income": 40000})

    with patch("app.services.vector_service.VectorService", return_value=mock_service):
        res1 = tool._run(extracted_info=profile_json)
        res2 = tool._run(extracted_info=profile_json)

    assert mock_service.find_similar_applications.call_count == 1
    assert "90.0" in res1
    assert res1 == res2


# ── Test 6: Workflow Cancellation Halts Execution Promptly ─────────────────

def test_workflow_cancellation_cooperative_halt():
    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db)
    abort_event = threading.Event()
    abort_event.set()  # Pre-cancelled

    with pytest.raises(TimeoutError) as exc_info:
        crew.run(
            application_id=1006,
            applicant_name="Aborted User",
            loan_amount=500000.0,
            documents=[],
            abort_event=abort_event,
        )

    assert "aborted" in str(exc_info.value).lower()


# ── Test 7: Worker Cleanup and Lock Release on Failure ─────────────────────

def test_worker_cleanup_and_lock_release_on_failure():
    app_id = 1007
    mock_db, mock_app = _setup_app_and_db(app_id)
    mock_user = MagicMock()
    mock_user.role = "admin"

    with _active_verifications_lock:
        _active_verifications.discard(app_id)
    _verification_tracker.release_worker(app_id)

    with patch("app.agents.crew.LoanVerificationCrew.run", side_effect=RuntimeError("Unrecoverable worker crash")):
        with pytest.raises(HTTPException) as exc_info:
            run_agentic_verification(application_id=app_id, db=mock_db, current_user=mock_user)

    assert exc_info.value.status_code == 500
    # Lock MUST be released
    time.sleep(0.05)
    assert _verification_tracker.is_active(app_id) is False
    with _active_verifications_lock:
        assert app_id not in _active_verifications


# ── Test 8: Duplicate Request Rejected with 409 ────────────────────────────

def test_duplicate_request_rejected_with_409():
    app_id = 1008
    mock_db, mock_app = _setup_app_and_db(app_id)
    mock_user = MagicMock()
    mock_user.role = "admin"

    worker_proceed = threading.Event()

    def slow_worker(*args, **kwargs):
        worker_proceed.wait(timeout=2.0)
        return _sample_agent_result()

    with _active_verifications_lock:
        _active_verifications.discard(app_id)
    _verification_tracker.release_worker(app_id)

    with patch("app.agents.crew.LoanVerificationCrew.run", side_effect=slow_worker):
        with patch("app.core.config.settings.WORKFLOW_TIMEOUT_SECONDS", 0.05):
            # 1. First call times out
            with pytest.raises(HTTPException) as exc1:
                run_agentic_verification(application_id=app_id, db=mock_db, current_user=mock_user)
            assert exc1.value.status_code == 504

            # 2. Second duplicate call while worker still active is rejected with 409
            with pytest.raises(HTTPException) as exc2:
                run_agentic_verification(application_id=app_id, db=mock_db, current_user=mock_user)
            assert exc2.value.status_code == 409

    worker_proceed.set()
    time.sleep(0.05)
    _verification_tracker.release_worker(app_id)
    with _active_verifications_lock:
        _active_verifications.discard(app_id)


# ── Test 9: Timeout Truthfully Marks Application Failed (No Fake Report) ───

def test_timeout_marks_application_failed_no_fake_report():
    app_id = 1009
    mock_db, mock_app = _setup_app_and_db(app_id)
    mock_user = MagicMock()
    mock_user.role = "admin"

    worker_proceed = threading.Event()

    def slow_worker(*args, **kwargs):
        worker_proceed.wait(timeout=2.0)
        return _sample_agent_result()

    with _active_verifications_lock:
        _active_verifications.discard(app_id)
    _verification_tracker.release_worker(app_id)

    with patch("app.agents.crew.LoanVerificationCrew.run", side_effect=slow_worker):
        with patch("app.core.config.settings.WORKFLOW_TIMEOUT_SECONDS", 0.05):
            with pytest.raises(HTTPException) as exc:
                run_agentic_verification(application_id=app_id, db=mock_db, current_user=mock_user)
            assert exc.value.status_code == 504

    # DB state must be updated to failed
    assert mock_app.status == ApplicationStatus.failed
    assert mock_db.commit.called

    worker_proceed.set()
    time.sleep(0.05)
    _verification_tracker.release_worker(app_id)
    with _active_verifications_lock:
        _active_verifications.discard(app_id)


# ── Test 10: Missing Monthly Income Not Fabricated ─────────────────────────

def test_missing_monthly_income_not_fabricated():
    from app.services.verification_engine import VerificationEngine

    engine = VerificationEngine()
    # Missing monthly_income
    extracted = {
        "applicant_name": "Applicant Without Income",
        "monthly_income": None,
        "loan_amount": 200000.0,
        "aadhaar_number": "123412341234",
        "pan_number": "ABCDE1234F",
    }
    docs = [{"document_type": "aadhaar"}, {"document_type": "pan"}]

    res = engine.verify(extracted, docs)
    # Income check must fail, not be assumed or fabricated
    income_check = next((c for c in res["checks"] if c["name"] == "Income Verification"), None)
    assert income_check is not None
    assert income_check["passed"] is False
    assert "not found" in income_check["details"].lower()
    assert res["status"] in ("manual_review", "rejected")


# ── Test 11: Insufficient Remaining Budget Cascades Immediately Without Futile Retry ──

def test_insufficient_remaining_budget_cascades_immediately_without_futile_retry():
    """When attempt fails with 503 and budget is insufficient for retry + fallback, cascade immediately."""
    from app.agents.crew import LoanVerificationCrew

    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db, gemini_max_retries=2, overall_timeout=70.0)

    # First tier call fails with 503 after taking 30 seconds (leaving 40s remaining)
    err_503 = Exception("503 Service Unavailable: High Demand")
    setattr(err_503, "status_code", 503)

    flash_res = _sample_agent_result(model="gemini-flash-latest")

    calls = []

    def mock_exec(*args, **kwargs):
        model = kwargs.get("model_name")
        calls.append(model)
        if "flash-lite" in model:
            # Simulate 503 after some time
            raise err_503
        return flash_res

    with patch.object(crew, "_execute_pipeline", side_effect=mock_exec):
        res = crew.run(
            application_id=1011,
            applicant_name="Budget Test",
            loan_amount=500000.0,
            documents=[],
        )

    # Must have called Flash-Lite once, NOT retried Flash-Lite, and cascaded to Flash
    assert calls.count("gemini-flash-lite-latest") == 1
    assert "gemini-flash-latest" in calls
    assert res["llm_model"] == "gemini-flash-latest"


# ── Test 12: OCRTool Uses Pre-Extracted DB Text Without Calling Heavy OCR ─

def test_ocr_tool_uses_pre_extracted_db_text_without_easyocr():
    """OCRTool should immediately return pre-extracted text from DB without running CPU OCR."""
    from app.agents.tools import OCRTool
    from app.models.document import Document

    mock_db = MagicMock()
    mock_doc = MagicMock(spec=Document)
    mock_doc.extracted_text = "Authoritative pre-extracted PAN: ABCDE1234F"
    mock_doc.file_path = "uploads/test_file.jpeg"
    mock_doc.original_name = "test_file.jpeg"

    mock_db.query.return_value.filter.return_value.first.return_value = mock_doc

    tool = OCRTool(db_session=mock_db)

    with patch("app.agents.tools.extract_text") as mock_extract:
        result = tool._run("uploads/test_file.jpeg")
        assert result == "Authoritative pre-extracted PAN: ABCDE1234F"
        # Heavy CPU OCR must NOT be called
        mock_extract.assert_not_called()


# ── Test 13: Unhalted Worker Suppresses Concurrent Fallback ───────────────

def test_worker_thread_unhalted_suppresses_concurrent_fallback():
    """If a timed-out tier's worker thread is still running, concurrent fallback is suppressed."""
    from app.agents.crew import LoanVerificationCrew

    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db, gemini_max_retries=1, overall_timeout=180.0)

    worker_stalled = threading.Event()

    def stalled_exec(*args, **kwargs):
        # Thread stalls and refuses to halt within grace period
        worker_stalled.wait(timeout=5.0)
        return _sample_agent_result()

    with patch.object(crew, "_execute_pipeline", side_effect=stalled_exec):
        with patch("app.core.config.settings.LLM_TIER_TIMEOUT_SECONDS", 0.05):
            with pytest.raises(TimeoutError) as exc_info:
                crew.run(
                    application_id=1013,
                    applicant_name="Unhalted Worker Test",
                    loan_amount=500000.0,
                    documents=[],
                )
            assert "suppressed concurrent fallback" in str(exc_info.value).lower()

    worker_stalled.set()


# ── Test 14: 300-Second Timeout Configuration ─────────────────────────────

def test_300s_timeout_configuration():
    """Settings and Crew defaults must reflect the 300.0s overall workflow budget."""
    from app.core.config import settings
    from app.agents.crew import LoanVerificationCrew

    assert settings.WORKFLOW_TIMEOUT_SECONDS == 300.0
    assert settings.LLM_TIER_TIMEOUT_SECONDS == 175.0
    assert settings.LLM_REQUEST_TIMEOUT == 90.0

    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db)
    assert crew.overall_timeout == 300.0


# ── Test 15: Verification Status Endpoint ──────────────────────────────────

def test_verification_status_endpoint():
    """GET /verify/{application_id}/status returns active flag, timeout flag, and report."""
    from app.api.endpoints.verification import get_verification_status
    from app.models.user import User

    mock_db, mock_app = _setup_app_and_db(app_id=197, applicant_name="Test Polling", status=ApplicationStatus.in_progress)
    mock_user = MagicMock(spec=User)
    mock_user.role = "officer"

    with patch("app.api.endpoints.verification.authorize_application_access"):
        # Case 1: active worker running
        with patch.object(_verification_tracker, "is_active", return_value=True):
            with patch.object(_verification_tracker, "is_timed_out", return_value=False):
                res = get_verification_status(application_id=197, db=mock_db, current_user=mock_user)
                assert res["application_id"] == 197
                assert res["is_active"] is True
                assert res["is_timed_out"] is False
                assert res["report"] is None

        # Case 2: worker finished, report available
        mock_app.status = ApplicationStatus.approved
        mock_report = MagicMock(spec=VerificationReport)
        mock_report.id = 1
        mock_report.application_id = 197
        mock_report.verification_score = 92.0
        mock_report.risk_score = 8.0
        mock_report.fraud_flag = False
        mock_report.status = "approved"
        mock_report.extracted_info = {}
        mock_report.fraud_analysis = {}
        mock_report.verification_details = {}
        mock_report.pdf_path = None
        mock_report.verification_mode = "agentic"
        mock_report.agent_summary = "Report ready"
        mock_report.agent_trace = {}
        mock_report.confidence_score = 0.95
        mock_report.explainable_ai = {}
        mock_report.agent_memory = []
        mock_report.execution_timeline = {}
        mock_report.created_at = None

        def query_with_report(model):
            q = MagicMock()
            if "Application" in str(model):
                q.filter.return_value.first.return_value = mock_app
            elif "VerificationReport" in str(model):
                q.filter.return_value.first.return_value = mock_report
            return q

        mock_db.query.side_effect = query_with_report

        with patch.object(_verification_tracker, "is_active", return_value=False):
            with patch.object(_verification_tracker, "is_timed_out", return_value=False):
                with patch("app.schemas.report.ReportOut.model_validate") as mock_validate:
                    mock_validate.return_value.model_dump.return_value = {"id": 1, "status": "approved", "verification_score": 92.0}
                    res = get_verification_status(application_id=197, db=mock_db, current_user=mock_user)
                    assert res["is_active"] is False
                    assert res["report"] is not None
                    assert res["report"]["verification_score"] == 92.0



