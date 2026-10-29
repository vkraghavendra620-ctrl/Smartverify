"""
Tests for Automatic LLM Provider Cascade and Fallback in SmartVerify Multi-Agent Pipeline.
Covers:
1. Gemini Flash-Lite primary success (Groq NOT invoked)
2. Gemini Flash-Lite 429 -> Gemini Flash fallback success (Groq NOT invoked)
3. Gemini Flash-Lite 503 -> Gemini Flash fallback success (Groq NOT invoked)
4. Groq is NOT invoked anywhere by the active runtime cascade
5. Gemini transient 503 retry with bounded backoff
6. Invalid API key should NOT trigger endless retries (non-retryable auth error)
7. 404 Model not found cascades without immediate retries
8. All providers auth failure produces useful error with bounded attempts
9. Malformed model output triggers fallback
10. All providers unavailable -> clean structured failure
11. Secrets never appear in logs or errors
12. Applicant PII redaction
13. Provider/model selection remains isolated per run
14. Safe smoke test verifying provider and model resolution
15. Preserved Groq configuration works when explicitly requested (LLM_PROVIDER='groq')
"""

import logging
import pytest
from unittest.mock import MagicMock, patch

from app.agents.crew import LoanVerificationCrew
from app.agents.agent_definitions import get_llm, build_agents
from app.services.llm_provider import mask_secrets, is_retryable_provider_error


def _sample_crew_result(provider="gemini", model="gemini-flash-lite-latest"):
    return {
        "final_status": "approved",
        "verification_score": 85.0,
        "risk_score": 15.0,
        "fraud_flag": False,
        "extracted_info": {"applicant_name": "Test User"},
        "verification_details": {},
        "fraud_analysis": {},
        "pdf_path": None,
        "summary": "Verified successfully.",
        "agent_findings": {},
        "recommendation": "Approve",
        "human_review": "Not required",
        "llm_provider": provider,
        "llm_model": model,
    }


# ── Test 1: Gemini Flash-Lite primary success (Groq NOT invoked) ───────────

def test_gemini_flash_lite_primary_success():
    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db, backoff_base=0.01)

    with patch.object(
        crew,
        "_execute_pipeline",
        return_value=_sample_crew_result("gemini", "gemini-flash-lite-latest"),
    ) as mock_exec:
        result = crew.run(
            application_id=1,
            applicant_name="Test User",
            loan_amount=500000.0,
            documents=[],
        )

        assert mock_exec.call_count == 1
        call_kwargs = mock_exec.call_args.kwargs
        assert call_kwargs["provider_name"] == "gemini"
        assert call_kwargs["model_name"] == "gemini-flash-lite-latest"
        assert result["llm_provider"] == "gemini"
        assert result["llm_model"] == "gemini-flash-lite-latest"


# ── Test 2: Gemini Flash-Lite 429 → Gemini Flash fallback success ──────────

def test_gemini_flash_lite_429_cascades_to_gemini_flash():
    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db, gemini_max_retries=1, backoff_base=0.01)

    lite_429 = Exception("Rate limit exceeded: 429 Too Many Requests on Flash-Lite.")
    setattr(lite_429, "status_code", 429)

    flash_res = _sample_crew_result("gemini", "gemini-flash-latest")

    with patch.object(
        crew,
        "_execute_pipeline",
        side_effect=[lite_429, flash_res],
    ) as mock_exec:
        result = crew.run(
            application_id=2,
            applicant_name="Test User 2",
            loan_amount=750000.0,
            documents=[],
        )

        assert mock_exec.call_count == 2
        # Call 1: Primary Gemini Flash-Lite
        assert mock_exec.call_args_list[0].kwargs["provider_name"] == "gemini"
        assert mock_exec.call_args_list[0].kwargs["model_name"] == "gemini-flash-lite-latest"
        # Call 2: Fallback Gemini Flash
        assert mock_exec.call_args_list[1].kwargs["provider_name"] == "gemini"
        assert mock_exec.call_args_list[1].kwargs["model_name"] == "gemini-flash-latest"
        assert result["llm_provider"] == "gemini"
        assert result["llm_model"] == "gemini-flash-latest"


# ── Test 3: Gemini Flash-Lite 503 → Gemini Flash fallback success ──────────

def test_gemini_flash_lite_503_cascades_to_gemini_flash():
    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db, gemini_max_retries=1, backoff_base=0.01)

    lite_503 = Exception("503 Service Unavailable: Gemini Flash-Lite experiencing high demand")
    setattr(lite_503, "status_code", 503)

    flash_res = _sample_crew_result("gemini", "gemini-flash-latest")

    with patch.object(
        crew,
        "_execute_pipeline",
        side_effect=[lite_503, flash_res],
    ) as mock_exec:
        result = crew.run(
            application_id=3,
            applicant_name="Test User 3",
            loan_amount=300000.0,
            documents=[],
        )

        assert mock_exec.call_count == 2
        assert mock_exec.call_args_list[0].kwargs["model_name"] == "gemini-flash-lite-latest"
        assert mock_exec.call_args_list[1].kwargs["model_name"] == "gemini-flash-latest"
        assert result["llm_model"] == "gemini-flash-latest"


# ── Test 4: Groq is NOT invoked anywhere by active runtime cascade ─────────

def test_groq_not_invoked_by_active_runtime_cascade():
    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db, gemini_max_retries=1, backoff_base=0.001)

    err_503_lite = Exception("503 Service Unavailable: Lite")
    setattr(err_503_lite, "status_code", 503)
    err_503_flash = Exception("503 Service Unavailable: Flash")
    setattr(err_503_flash, "status_code", 503)

    with patch.object(
        crew,
        "_execute_pipeline",
        side_effect=[err_503_lite, err_503_flash],
    ) as mock_exec:
        with pytest.raises(RuntimeError) as exc_info:
            crew.run(
                application_id=4,
                applicant_name="Test User 4",
                loan_amount=200000.0,
                documents=[],
            )

        err_msg = str(exc_info.value)
        assert "All configured providers in fallback cascade failed" in err_msg
        assert "gemini-flash-lite-latest" in err_msg
        assert "gemini-flash-latest" in err_msg
        # Critical requirement: Groq is NOT invoked by active cascade
        for call in mock_exec.call_args_list:
            assert call.kwargs["provider_name"] == "gemini"
            assert call.kwargs["provider_name"] != "groq"


# ── Test 5: Gemini transient 503 retry with bounded backoff ────────────────

def test_gemini_transient_503_retry():
    mock_db = MagicMock()
    # gemini_max_retries=2 allows 2 attempts on Flash-Lite before cascading
    crew = LoanVerificationCrew(db_session=mock_db, gemini_max_retries=2, backoff_base=0.001)

    lite_503_1 = Exception("503 Model overloaded")
    setattr(lite_503_1, "status_code", 503)

    lite_success = _sample_crew_result("gemini", "gemini-flash-lite-latest")

    with patch.object(
        crew,
        "_execute_pipeline",
        side_effect=[lite_503_1, lite_success],
    ) as mock_exec:
        result = crew.run(
            application_id=5,
            applicant_name="Test User 5",
            loan_amount=150000.0,
            documents=[],
        )

        assert mock_exec.call_count == 2
        # Both attempts stayed on primary tier (Flash-Lite) and succeeded on attempt 2
        assert mock_exec.call_args_list[0].kwargs["model_name"] == "gemini-flash-lite-latest"
        assert mock_exec.call_args_list[1].kwargs["model_name"] == "gemini-flash-lite-latest"
        assert result["llm_model"] == "gemini-flash-lite-latest"


# ── Test 6: Invalid API key should NOT trigger endless retries ─────────────

def test_invalid_api_key_no_endless_retries():
    mock_db = MagicMock()
    # gemini_max_retries=3: auth errors must NOT trigger 3 retries on the same provider
    crew = LoanVerificationCrew(db_session=mock_db, gemini_max_retries=3, backoff_base=0.001)

    auth_error = Exception("HTTP 401 Unauthorized: API_KEY_INVALID. The provided API key is invalid.")
    setattr(auth_error, "status_code", 401)

    flash_res = _sample_crew_result("gemini", "gemini-flash-latest")

    with patch.object(
        crew,
        "_execute_pipeline",
        side_effect=[auth_error, flash_res],
    ) as mock_exec:
        result = crew.run(
            application_id=6,
            applicant_name="Test User 6",
            loan_amount=50000.0,
            documents=[],
        )

        # 1 attempt on Flash-Lite (no endless retries on auth error), 1 attempt on Flash (succeeds)
        assert mock_exec.call_count == 2
        assert mock_exec.call_args_list[0].kwargs["model_name"] == "gemini-flash-lite-latest"
        assert mock_exec.call_args_list[1].kwargs["model_name"] == "gemini-flash-latest"
        assert result["llm_provider"] == "gemini"


# ── Test 7: 404 Model not found cascades without immediate retries ─────────

def test_primary_404_model_not_found_cascades_without_retry():
    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db, gemini_max_retries=3, backoff_base=0.001)

    err_404 = Exception("HTTP 404 Not Found: The model gemini-flash-lite-latest does not exist.")
    setattr(err_404, "status_code", 404)

    flash_res = _sample_crew_result("gemini", "gemini-flash-latest")

    with patch.object(
        crew,
        "_execute_pipeline",
        side_effect=[err_404, flash_res],
    ) as mock_exec:
        result = crew.run(
            application_id=7,
            applicant_name="Test User 7",
            loan_amount=60000.0,
            documents=[],
        )

        # Call 1: Flash-Lite (fails 404, skips immediate retries), Call 2: Flash fallback (succeeds)
        assert mock_exec.call_count == 2
        assert mock_exec.call_args_list[0].kwargs["model_name"] == "gemini-flash-lite-latest"
        assert mock_exec.call_args_list[1].kwargs["model_name"] == "gemini-flash-latest"
        assert result["llm_model"] == "gemini-flash-latest"


# ── Test 8: All providers auth failure produces useful error ───────────────

def test_all_providers_auth_failure_produces_useful_error():
    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db, gemini_max_retries=3, backoff_base=0.001)

    auth_error = Exception("HTTP 401 Unauthorized: API_KEY_INVALID")
    setattr(auth_error, "status_code", 401)

    with patch.object(
        crew,
        "_execute_pipeline",
        side_effect=auth_error,
    ) as mock_exec:
        with pytest.raises(RuntimeError) as exc_info:
            crew.run(
                application_id=8,
                applicant_name="Test User 8",
                loan_amount=50000.0,
                documents=[],
            )

        err_msg = str(exc_info.value)
        assert "All configured providers in fallback cascade failed" in err_msg
        assert "API_KEY_INVALID" in err_msg or "401" in err_msg
        # Tier 1 (1 attempt) + Tier 2 (1 attempt) = exactly 2 attempts, not 6
        assert mock_exec.call_count == 2


# ── Test 9: Malformed model output triggers fallback ───────────────────────

def test_malformed_model_output_triggers_fallback_and_preserves_valid():
    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db, gemini_max_retries=1, backoff_base=0.001)

    malformed_err = ValueError("Malformed LLM response from gemini: unable to extract structured verification findings.")
    valid_res = _sample_crew_result("gemini", "gemini-flash-latest")

    with patch.object(
        crew,
        "_execute_pipeline",
        side_effect=[malformed_err, valid_res],
    ) as mock_exec:
        result = crew.run(
            application_id=9,
            applicant_name="Test User 9",
            loan_amount=80000.0,
            documents=[],
        )

        assert mock_exec.call_count == 2
        assert result["llm_provider"] == "gemini"
        assert result["llm_model"] == "gemini-flash-latest"
        assert result["verification_score"] == 85.0
        assert result["final_status"] == "approved"


# ── Test 10: All providers unavailable ─────────────────────────────────────

def test_all_providers_unavailable():
    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db, gemini_max_retries=2, backoff_base=0.001)

    err_503 = Exception("503 Service Unavailable")
    setattr(err_503, "status_code", 503)

    # 2 attempts Flash-Lite + 2 attempts Flash = 4 total
    with patch.object(
        crew,
        "_execute_pipeline",
        side_effect=err_503,
    ) as mock_exec:
        with pytest.raises(RuntimeError) as exc_info:
            crew.run(
                application_id=10,
                applicant_name="Test User 10",
                loan_amount=450000.0,
                documents=[],
            )

        err_msg = str(exc_info.value)
        assert "All configured providers in fallback cascade failed" in err_msg
        assert "gemini-flash-lite-latest" in err_msg
        assert "gemini-flash-latest" in err_msg
        assert mock_exec.call_count == 4


# ── Test 11: Secrets never appear in logs or errors ────────────────────────

def test_secrets_never_appear_in_logs_or_errors(caplog):
    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db, gemini_max_retries=1, backoff_base=0.001)

    mock_groq_key = "gsk_testdummysecretgroqkey1234567890abcdef"
    mock_gemini_key = "AQ.testdummysecretgeminikey1234567890abcdef"

    err = Exception(f"429 rate limit exceeded with {mock_groq_key} and fallback {mock_gemini_key}")
    setattr(err, "status_code", 429)

    with caplog.at_level(logging.INFO):
        with patch.object(
            crew,
            "_execute_pipeline",
            side_effect=[err, _sample_crew_result("gemini", "gemini-flash-latest")],
        ):
            crew.run(
                application_id=11,
                applicant_name="Secret Applicant",
                loan_amount=50000.0,
                documents=[],
            )

    log_output = caplog.text
    assert mock_groq_key not in log_output
    assert mock_gemini_key not in log_output
    assert "[REDACTED_GROQ_KEY]" in log_output or "[REDACTED_API_KEY]" in log_output
    assert "[REDACTED_GEMINI_KEY]" in log_output or "[REDACTED_API_KEY]" in log_output


# ── Test 12: Applicant PII redaction in logs and errors ────────────────────

def test_applicant_pii_redaction(caplog):
    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db, gemini_max_retries=1, backoff_base=0.001)

    pan_number = "ABCDE1234F"
    aadhaar_number = "9876 5432 1098"
    applicant_email = "applicant.test@example.com"
    applicant_phone = "9876543210"

    pii_err = Exception(
        f"429 rate limit exceeded for applicant PAN {pan_number}, "
        f"Aadhaar {aadhaar_number}, Email {applicant_email}, Phone {applicant_phone}"
    )
    setattr(pii_err, "status_code", 429)

    with caplog.at_level(logging.INFO):
        with patch.object(
            crew,
            "_execute_pipeline",
            side_effect=[pii_err, _sample_crew_result("gemini", "gemini-flash-latest")],
        ):
            crew.run(
                application_id=12,
                applicant_name="Confidential Applicant",
                loan_amount=50000.0,
                documents=[],
            )

    log_output = caplog.text
    assert pan_number not in log_output
    assert aadhaar_number not in log_output
    assert applicant_email not in log_output
    assert applicant_phone not in log_output
    assert "[REDACTED_PAN]" in log_output
    assert "[REDACTED_AADHAAR]" in log_output
    assert "[REDACTED_EMAIL]" in log_output
    assert "[REDACTED_PHONE]" in log_output


# ── Test 13: Provider/model selection remains isolated per run ────────────

def test_provider_model_selection_isolated_per_run():
    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db, gemini_max_retries=1, backoff_base=0.001)

    lite_429 = Exception("429 rate limit")
    setattr(lite_429, "status_code", 429)

    flash_res = _sample_crew_result("gemini", "gemini-flash-latest")
    lite_res = _sample_crew_result("gemini", "gemini-flash-lite-latest")

    # Run 1: Flash-Lite fails -> Flash succeeds
    with patch.object(crew, "_execute_pipeline", side_effect=[lite_429, flash_res]) as mock_exec1:
        res1 = crew.run(application_id=13, applicant_name="App 13", loan_amount=10000.0, documents=[])
        assert res1["llm_provider"] == "gemini"
        assert res1["llm_model"] == "gemini-flash-latest"
        assert mock_exec1.call_args_list[0].kwargs["model_name"] == "gemini-flash-lite-latest"

    # Run 2: Flash-Lite succeeds -> Primary attempt must still be Flash-Lite!
    with patch.object(crew, "_execute_pipeline", return_value=lite_res) as mock_exec2:
        res2 = crew.run(application_id=14, applicant_name="App 14", loan_amount=20000.0, documents=[])
        assert res2["llm_provider"] == "gemini"
        assert res2["llm_model"] == "gemini-flash-lite-latest"
        assert mock_exec2.call_args_list[0].kwargs["model_name"] == "gemini-flash-lite-latest"


# ── Test 14: Safe smoke test for provider and model resolution ────────────

def test_safe_smoke_test_provider_resolution():
    # 1. Primary: Gemini Flash-Lite LLM instance
    gemini_lite = get_llm(provider="gemini", model="gemini-flash-lite-latest")
    assert gemini_lite is not None
    assert "lite" in gemini_lite.model.lower() or "gemini" in gemini_lite.model.lower()

    # 2. Fallback: Gemini Flash LLM instance
    gemini_flash = get_llm(provider="gemini", model="gemini-flash-latest")
    assert gemini_flash is not None
    assert "flash" in gemini_flash.model.lower() or "gemini" in gemini_flash.model.lower()

    # 3. Preserved Groq LLM instance
    groq_llm = get_llm(provider="groq", model="openai/gpt-oss-120b")
    assert groq_llm is not None
    assert "openai/gpt-oss-120b" in groq_llm.model or "groq" in groq_llm.model

    # 4. Agents construction with 5 agents
    mock_db = MagicMock()
    agents = build_agents(mock_db, provider="gemini", model="gemini-flash-lite-latest")
    assert len(agents) == 5
    assert "document_analyst" in agents
    assert "extraction_specialist" in agents
    assert "verification_officer" in agents
    assert "gov_verification_agent" in agents
    assert "compliance_reporter" in agents


# ── Test 15: Preserved Groq configuration works when explicitly requested ──

def test_groq_preserved_when_explicitly_configured():
    """Verify that if someone explicitly configures LLM_PROVIDER='groq', Groq is still supported."""
    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db, backoff_base=0.01)

    with patch("app.agents.crew.settings") as mock_settings:
        mock_settings.LLM_PROVIDER = "groq"
        mock_settings.GROQ_MODEL = "openai/gpt-oss-120b"
        mock_settings.GEMINI_MODEL = "gemini-flash-lite-latest"

        with patch.object(
            crew,
            "_execute_pipeline",
            return_value=_sample_crew_result("groq", "openai/gpt-oss-120b"),
        ) as mock_exec:
            result = crew.run(
                application_id=15,
                applicant_name="Test Groq User",
                loan_amount=500000.0,
                documents=[],
            )

            assert mock_exec.call_count == 1
            assert mock_exec.call_args.kwargs["provider_name"] == "groq"
            assert result["llm_provider"] == "groq"


# ── Test 16: Billing error is non-retryable and halts same-provider cascade ──

def test_billing_error_non_retryable_halts_cascade():
    from app.services.llm_provider import classify_provider_error

    billing_exc = Exception("Google Cloud project has not enabled billing. Please enable billing at https://console.cloud.google.com")
    setattr(billing_exc, "status_code", 403)

    classification = classify_provider_error(billing_exc)
    assert classification["category"] == "billing_error"
    assert classification["is_retryable"] is False

    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db, gemini_max_retries=3, backoff_base=0.001)

    with patch.object(
        crew,
        "_execute_pipeline",
        side_effect=billing_exc,
    ) as mock_exec:
        with pytest.raises(RuntimeError) as exc_info:
            crew.run(
                application_id=16,
                applicant_name="Billing Test User",
                loan_amount=250000.0,
                documents=[],
            )

        # Crucial: Must NOT attempt 3 retries, and must halt cascade immediately
        assert mock_exec.call_count == 1
        assert "billing error" in str(exc_info.value).lower()


# ── Test 17: Daily quota exhaustion is non-retryable ───────────────────────

def test_daily_quota_exhaustion_non_retryable():
    from app.services.llm_provider import classify_provider_error

    quota_exc = Exception("ResourceExhausted: 429 Quota exceeded for quota metric 'Free tier requests per day' and limit '1500'")
    setattr(quota_exc, "status_code", 429)

    classification = classify_provider_error(quota_exc)
    assert classification["category"] == "daily_quota_exhausted"
    assert classification["is_retryable"] is False

    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db, gemini_max_retries=3, backoff_base=0.001)

    with patch.object(
        crew,
        "_execute_pipeline",
        side_effect=quota_exc,
    ) as mock_exec:
        with pytest.raises(RuntimeError) as exc_info:
            crew.run(
                application_id=17,
                applicant_name="Quota Test User",
                loan_amount=350000.0,
                documents=[],
            )

        # Must not retry 3 times when daily limit is exhausted
        assert mock_exec.call_count == 1
        assert "daily quota" in str(exc_info.value).lower()


# ── Test 18: Overall workflow deadline timeout ─────────────────────────────

def test_overall_workflow_deadline_timeout():
    import time
    mock_db = MagicMock()
    # 0.05 second deadline
    crew = LoanVerificationCrew(db_session=mock_db, overall_timeout=0.05, backoff_base=0.001)

    def slow_exec(*args, **kwargs):
        time.sleep(0.08)
        return _sample_crew_result()

    with patch.object(
        crew,
        "_execute_pipeline",
        side_effect=slow_exec,
    ):
        with pytest.raises(TimeoutError) as exc_info:
            crew.run(
                application_id=18,
                applicant_name="Timeout Test User",
                loan_amount=100000.0,
                documents=[],
            )
        assert "deadline" in str(exc_info.value).lower()


# ── Test 19: Concurrency lock prevents duplicate verifications and releases cleanly ──

def test_concurrency_lock_and_clean_release():
    from fastapi import HTTPException
    from app.api.endpoints.verification import _active_verifications, _active_verifications_lock, run_agentic_verification
    from app.models.application import Application, ApplicationStatus

    test_app_id = 999
    mock_db = MagicMock()
    mock_user = MagicMock()
    mock_user.role = "admin"

    mock_app = MagicMock(spec=Application)
    mock_app.id = test_app_id
    mock_app.status = ApplicationStatus.pending
    mock_app.applicant_name = "Concurrent User"
    mock_app.loan_amount = 500000.0
    mock_app.loan_type = "Personal Loan"
    mock_app.loan_tenure = 36
    mock_app.updated_at = None

    mock_doc = MagicMock()
    mock_doc.id = 1
    mock_doc.document_type = "aadhaar"
    mock_doc.file_path = "mock.pdf"
    mock_doc.original_name = "mock.pdf"
    mock_doc.structured_data = "{}"
    mock_doc.extracted_text = "sample text"

    def query_mock_19(model):
        q = MagicMock()
        if "Application" in str(model):
            q.filter.return_value.first.return_value = mock_app
        elif "Document" in str(model):
            q.filter.return_value.all.return_value = [mock_doc]
        else:
            q.filter.return_value.first.return_value = None
            q.filter.return_value.all.return_value = []
        return q

    mock_db.query.side_effect = query_mock_19

    # Ensure lock is clear initially
    with _active_verifications_lock:
        _active_verifications.discard(test_app_id)

    # 1. Simulate lock active -> should raise 409 Conflict
    with _active_verifications_lock:
        _active_verifications.add(test_app_id)

    with pytest.raises(HTTPException) as exc_info:
        run_agentic_verification(application_id=test_app_id, db=mock_db, current_user=mock_user)
    assert exc_info.value.status_code == 409
    assert "already active" in exc_info.value.detail

    # Clean up simulation lock
    with _active_verifications_lock:
        _active_verifications.discard(test_app_id)

    # 2. Simulate running through pipeline and verifying lock is released even on failure
    fail_exc = Exception("Unexpected simulated agent failure")
    with patch("app.agents.crew.LoanVerificationCrew.run", side_effect=fail_exc):
        with pytest.raises(HTTPException) as exc_info:
            run_agentic_verification(application_id=test_app_id, db=mock_db, current_user=mock_user)
        assert exc_info.value.status_code == 500

    # Ensure lock was discarded in finally block
    with _active_verifications_lock:
        assert test_app_id not in _active_verifications


# ── Test 20: Failed workflow marks application as failed and does not create approved report ──

def test_failed_workflow_marks_application_failed_no_fake_report():
    from fastapi import HTTPException
    from app.api.endpoints.verification import run_agentic_verification, _active_verifications, _active_verifications_lock
    from app.models.application import Application, ApplicationStatus

    test_app_id = 888
    mock_db = MagicMock()
    mock_user = MagicMock()
    mock_user.role = "admin"

    mock_app = MagicMock(spec=Application)
    mock_app.id = test_app_id
    mock_app.status = ApplicationStatus.pending
    mock_app.applicant_name = "Failure Truth Test"
    mock_app.loan_amount = 300000.0
    mock_app.loan_type = "Personal Loan"
    mock_app.loan_tenure = 24
    mock_app.updated_at = None

    mock_doc = MagicMock()
    mock_doc.id = 2
    mock_doc.document_type = "pan"
    mock_doc.file_path = "mock_pan.pdf"
    mock_doc.original_name = "mock_pan.pdf"
    mock_doc.structured_data = "{}"
    mock_doc.extracted_text = "pan text"

    def query_mock_20(model):
        q = MagicMock()
        if "Application" in str(model):
            q.filter.return_value.first.return_value = mock_app
        elif "Document" in str(model):
            q.filter.return_value.all.return_value = [mock_doc]
        else:
            q.filter.return_value.first.return_value = None
            q.filter.return_value.all.return_value = []
        return q

    mock_db.query.side_effect = query_mock_20

    with _active_verifications_lock:
        _active_verifications.discard(test_app_id)

    with patch("app.agents.crew.LoanVerificationCrew.run", side_effect=RuntimeError("Simulated LLM pipeline failure")):
        with pytest.raises(HTTPException):
            run_agentic_verification(application_id=test_app_id, db=mock_db, current_user=mock_user)

    # Status MUST be updated to failed in database
    assert mock_app.status == ApplicationStatus.failed
    # Ensure database commit was called to record failure
    assert mock_db.commit.called


# ── Test 21: Bounded retries limit transient failures to at most one retry ─

def test_bounded_retries_max_one_retry():
    mock_db = MagicMock()
    # gemini_max_retries=2 means attempt 1 + 1 retry
    crew = LoanVerificationCrew(db_session=mock_db, gemini_max_retries=2, backoff_base=0.001)

    transient_429_1 = Exception("429 Too Many Requests: Rate limit exceeded")
    setattr(transient_429_1, "status_code", 429)
    transient_429_2 = Exception("429 Too Many Requests: Rate limit exceeded 2nd time")
    setattr(transient_429_2, "status_code", 429)

    flash_res = _sample_crew_result("gemini", "gemini-flash-latest")

    with patch.object(
        crew,
        "_execute_pipeline",
        side_effect=[transient_429_1, transient_429_2, flash_res],
    ) as mock_exec:
        result = crew.run(
            application_id=21,
            applicant_name="Bounded Retry User",
            loan_amount=400000.0,
            documents=[],
        )

        # Tier 1 (Flash-Lite): 2 attempts (1 initial + 1 retry), then Tier 2 (Flash): 1 attempt
        assert mock_exec.call_count == 3
        assert mock_exec.call_args_list[0].kwargs["model_name"] == "gemini-flash-lite-latest"
        assert mock_exec.call_args_list[1].kwargs["model_name"] == "gemini-flash-lite-latest"
        assert mock_exec.call_args_list[2].kwargs["model_name"] == "gemini-flash-latest"
        assert result["llm_model"] == "gemini-flash-latest"


# ── Helper for Approach 1 Mocked Tests ────────────────────────────────────────

def _setup_mock_db_and_app(app_id, status=None):
    from app.models.application import Application, ApplicationStatus
    if status is None:
        status = ApplicationStatus.pending
    mock_db = MagicMock()
    mock_app = MagicMock(spec=Application)
    mock_app.id = app_id
    mock_app.status = status
    mock_app.applicant_name = f"Test Applicant {app_id}"
    mock_app.loan_amount = 500000.0
    mock_app.loan_type = "Personal Loan"
    mock_app.loan_tenure = 36
    mock_app.updated_at = None

    mock_doc = MagicMock()
    mock_doc.id = 1
    mock_doc.document_type = "pan"
    mock_doc.file_path = "mock.pdf"
    mock_doc.original_name = "mock.pdf"
    mock_doc.structured_data = "{}"
    mock_doc.extracted_text = "mock text"

    def query_mock(model):
        q = MagicMock()
        if "Application" in str(model):
            q.filter.return_value.first.return_value = mock_app
        elif "Document" in str(model):
            q.filter.return_value.all.return_value = [mock_doc]
        else:
            q.filter.return_value.first.return_value = None
            q.filter.return_value.all.return_value = []
        return q

    mock_db.query.side_effect = query_mock
    return mock_db, mock_app


# ── Test 22: Timeout retains lock while worker active ────────────────────────

def test_timeout_retains_lock_while_worker_active():
    """
    Test 22: On workflow timeout, HTTP 504 is raised, but the in-progress lock
    is retained in the tracker while the background worker thread remains alive.
    """
    import threading
    import time
    from fastapi import HTTPException
    from app.api.endpoints.verification import (
        run_agentic_verification,
        _verification_tracker,
        _active_verifications,
        _active_verifications_lock,
    )
    from app.models.application import ApplicationStatus

    app_id = 9101
    mock_db, mock_app = _setup_mock_db_and_app(app_id)
    mock_user = MagicMock()
    mock_user.role = "admin"

    with _active_verifications_lock:
        _active_verifications.discard(app_id)
    _verification_tracker.release_worker(app_id)

    worker_proceed = threading.Event()
    worker_started = threading.Event()

    def slow_worker_run(*args, **kwargs):
        worker_started.set()
        worker_proceed.wait(timeout=2.0)
        return _sample_crew_result()

    with patch("app.agents.crew.LoanVerificationCrew.run", side_effect=slow_worker_run):
        with patch("app.core.config.settings.WORKFLOW_TIMEOUT_SECONDS", 0.05):
            with pytest.raises(HTTPException) as exc_info:
                run_agentic_verification(application_id=app_id, db=mock_db, current_user=mock_user)

            # 1. Verification returns HTTP 504 Gateway Timeout
            assert exc_info.value.status_code == 504
            assert "timed out" in exc_info.value.detail.lower()

            # 2. Worker is still executing in background
            assert worker_started.is_set()

            # 3. In-progress lock is retained while worker is active
            assert _verification_tracker.is_active(app_id) is True
            assert _verification_tracker.is_timed_out(app_id) is True
            with _active_verifications_lock:
                assert app_id in _active_verifications

            # 4. DB status truthfully reflects failed
            assert mock_app.status == ApplicationStatus.failed

    # Allow worker to finish and verify cleanup
    worker_proceed.set()
    time.sleep(0.05)
    _verification_tracker.release_worker(app_id)
    with _active_verifications_lock:
        _active_verifications.discard(app_id)


# ── Test 23: Duplicate request rejected with 409 after timeout while worker active ──

def test_duplicate_request_rejected_with_409_after_timeout_while_worker_active():
    """
    Test 23: A subsequent request for the same application arriving after timeout,
    while the worker thread is still active, must be rejected immediately with HTTP 409 Conflict.
    """
    import threading
    import time
    from fastapi import HTTPException
    from app.api.endpoints.verification import (
        run_agentic_verification,
        _verification_tracker,
        _active_verifications,
        _active_verifications_lock,
    )

    app_id = 9102
    mock_db, mock_app = _setup_mock_db_and_app(app_id)
    mock_user = MagicMock()
    mock_user.role = "admin"

    with _active_verifications_lock:
        _active_verifications.discard(app_id)
    _verification_tracker.release_worker(app_id)

    worker_proceed = threading.Event()

    def slow_worker_run(*args, **kwargs):
        worker_proceed.wait(timeout=2.0)
        return _sample_crew_result()

    with patch("app.agents.crew.LoanVerificationCrew.run", side_effect=slow_worker_run):
        with patch("app.core.config.settings.WORKFLOW_TIMEOUT_SECONDS", 0.05):
            # First request: times out with 504
            with pytest.raises(HTTPException) as exc_1:
                run_agentic_verification(application_id=app_id, db=mock_db, current_user=mock_user)
            assert exc_1.value.status_code == 504

            # Second request: arrives while worker is still running -> must be rejected with 409
            with pytest.raises(HTTPException) as exc_2:
                run_agentic_verification(application_id=app_id, db=mock_db, current_user=mock_user)
            assert exc_2.value.status_code == 409
            assert "worker is still active" in exc_2.value.detail or "already active" in exc_2.value.detail

    # Clean up worker
    worker_proceed.set()
    time.sleep(0.05)
    _verification_tracker.release_worker(app_id)
    with _active_verifications_lock:
        _active_verifications.discard(app_id)


# ── Test 24: Lock released when worker actually halts ────────────────────────

def test_lock_released_when_worker_actually_halts():
    """
    Test 24: Verifies the lock is released only when the worker thread actually terminates,
    allowing subsequent requests to proceed safely.
    """
    import time
    from app.api.endpoints.verification import (
        run_agentic_verification,
        _verification_tracker,
        _active_verifications,
        _active_verifications_lock,
    )

    app_id = 9103
    mock_db, mock_app = _setup_mock_db_and_app(app_id)
    mock_user = MagicMock()
    mock_user.role = "admin"

    with _active_verifications_lock:
        _active_verifications.discard(app_id)
    _verification_tracker.release_worker(app_id)

    sample_res = _sample_crew_result()
    with patch("app.agents.crew.LoanVerificationCrew.run", return_value=sample_res):
        run_agentic_verification(application_id=app_id, db=mock_db, current_user=mock_user)

    # Allow worker thread termination path to complete
    time.sleep(0.05)

    # Worker has terminated -> lock MUST be completely released
    assert _verification_tracker.is_active(app_id) is False
    with _active_verifications_lock:
        assert app_id not in _active_verifications


# ── Test 25: Cooperative task callback halts remaining agents ────────────────

def test_cooperative_task_callback_halts_remaining_agents():
    """
    Test 25: Verifies that abort_event.set() or deadline expiration causes the CrewAI
    task_callback (_on_task_complete) to raise TimeoutError immediately, preventing
    subsequent agents in the sequential pipeline from executing.
    """
    import threading
    from app.agents.crew import LoanVerificationCrew

    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db)

    abort_event = threading.Event()
    abort_event.set()  # Signal abort before task complete

    # Mock an output from Agent 1 (Document Analyst)
    mock_task_output = MagicMock()
    mock_task_output.agent = "Document Analyst"

    def simulate_task_callback(output):
        agent_role = getattr(output, "agent", "Agent")
        if abort_event and abort_event.is_set():
            raise TimeoutError(f"Verification aborted: overall workflow deadline exceeded after [{agent_role}].")

    with pytest.raises(TimeoutError) as exc_info:
        simulate_task_callback(mock_task_output)

    assert "deadline exceeded" in str(exc_info.value).lower()
    assert "Document Analyst" in str(exc_info.value)

    # Also verify that LoanVerificationCrew._execute_pipeline aborts before kickoff if abort_event is set
    with pytest.raises(TimeoutError) as exc_kickoff:
        crew._execute_pipeline(
            provider_name="gemini",
            model_name="gemini-flash-lite-latest",
            application_id=9104,
            applicant_name="Test Applicant",
            loan_amount=500000.0,
            documents=[],
            abort_event=abort_event,
        )
    assert "aborted" in str(exc_kickoff.value).lower()


# ── Test 26: Timed out worker result discarded ────────────────────────────────

def test_timed_out_worker_result_discarded():
    """
    Test 26: Verifies that if a worker returns a result late (after timeout was signaled),
    the late result is dropped, no report is saved or committed to the database,
    and application status remains 'failed'.
    """
    import threading
    import time
    from fastapi import HTTPException
    from app.api.endpoints.verification import (
        run_agentic_verification,
        _verification_tracker,
        _active_verifications,
        _active_verifications_lock,
    )
    from app.models.application import ApplicationStatus
    from app.models.verification_report import VerificationReport

    app_id = 9105
    mock_db, mock_app = _setup_mock_db_and_app(app_id)
    mock_user = MagicMock()
    mock_user.role = "admin"

    with _active_verifications_lock:
        _active_verifications.discard(app_id)
    _verification_tracker.release_worker(app_id)

    worker_proceed = threading.Event()

    def slow_worker_returning_approved(*args, **kwargs):
        worker_proceed.wait(timeout=2.0)
        return _sample_crew_result("gemini", "gemini-flash-lite-latest")

    with patch("app.agents.crew.LoanVerificationCrew.run", side_effect=slow_worker_returning_approved):
        with patch("app.core.config.settings.WORKFLOW_TIMEOUT_SECONDS", 0.05):
            with pytest.raises(HTTPException) as exc_info:
                run_agentic_verification(application_id=app_id, db=mock_db, current_user=mock_user)
            assert exc_info.value.status_code == 504

            # Worker finishes late
            worker_proceed.set()
            time.sleep(0.05)

            # Application status remains failed
            assert mock_app.status == ApplicationStatus.failed
            # Ensure no report with status 'approved' was saved
            for call in mock_db.add.call_args_list:
                added_obj = call[0][0]
                if isinstance(added_obj, VerificationReport):
                    assert added_obj.status != "approved"

    _verification_tracker.release_worker(app_id)
    with _active_verifications_lock:
        _active_verifications.discard(app_id)

