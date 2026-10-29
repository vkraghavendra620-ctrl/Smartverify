"""
SmartVerify Loan Verification Crew
─────────────────────────────────────
Orchestrates the multi-agent pipeline:

    Document Analyst → Extraction Specialist → Verification Officer
                                              ↘ Fraud Investigator ↗
                                                       ↓
                                              Compliance Reporter

Produces a single structured result dict that mirrors the shape of the
previous (non-agentic) verification pipeline, so the FastAPI endpoint and
VerificationReport model don't need to change shape.
"""

import json
import logging
import os
import re
import threading
from typing import Any, Dict, List, Optional

from crewai import Crew, Process

from app.agents.agent_definitions import build_agents
from app.agents.tasks import build_tasks
from app.core.config import settings
from app.services.llm_provider import is_retryable_provider_error, is_provider_error, classify_provider_error

logger = logging.getLogger(__name__)


class LoanVerificationCrew:
    """Builds and runs the multi-agent loan verification crew for one application."""

    def __init__(
        self,
        db_session,
        gemini_max_retries: int = None,
        backoff_base: float = 1.0,
        overall_timeout: float = None,
    ):
        self.db = db_session
        self.gemini_max_retries = (
            gemini_max_retries
            if gemini_max_retries is not None
            else getattr(settings, "LLM_MAX_RETRIES", 2)
        )
        self.backoff_base = backoff_base
        self.overall_timeout = (
            overall_timeout
            if overall_timeout is not None
            else getattr(settings, "WORKFLOW_TIMEOUT_SECONDS", 300.0)
        )

    def run(
        self,
        application_id: int,
        applicant_name: str,
        loan_amount: float,
        documents: List[Dict[str, Any]],
        loan_type: str = "Home Loan",
        loan_tenure: int = 60,
        gov_verification_record: Dict[str, Any] = None,
        abort_event: Optional[threading.Event] = None,
    ) -> Dict[str, Any]:
        """
        Execute the crew with automatic 3-tier provider cascade:
        1. Primary: Groq (openai/gpt-oss-120b) [or Gemini if LLM_PROVIDER=gemini]
        2. Fallback 1: Gemini (gemini-flash-latest)
        3. Fallback 2: Gemini (gemini-flash-lite-latest)

        Enforces:
        - Strict overall workflow deadline (default 300s).
        - Cooperative abort via threading.Event between sequential agent tasks.
        - Correct classification: separates billing, daily quota, rate limits, auth, and server errors.
        - Never retries billing, authentication, or daily quota exhaustion.
        - Bounded backoff: at most one retry for transient errors, capped at 3.0s.
        - Drops futile cascades across same-provider accounts when account-wide billing/quota is exhausted.
        """
        import time

        if abort_event is None:
            abort_event = threading.Event()

        primary_provider = (
            getattr(settings, "LLM_PROVIDER", "gemini")
            or os.environ.get("LLM_PROVIDER", "gemini")
            or "gemini"
        ).strip().lower()

        gemini_primary_raw = (
            getattr(settings, "GEMINI_MODEL", "")
            or os.environ.get("GEMINI_MODEL")
            or "gemini-flash-lite-latest"
        )
        gemini_primary_clean = gemini_primary_raw.split("/", 1)[1] if gemini_primary_raw.startswith("gemini/") else gemini_primary_raw
        if not gemini_primary_clean or gemini_primary_clean == "gemini-3.5-flash-lite":
            gemini_primary_clean = "gemini-flash-lite-latest"

        gemini_fallback_raw = (
            getattr(settings, "GEMINI_FALLBACK_MODEL", "")
            or os.environ.get("GEMINI_FALLBACK_MODEL")
            or "gemini-flash-latest"
        )
        gemini_fallback_clean = gemini_fallback_raw.split("/", 1)[1] if gemini_fallback_raw.startswith("gemini/") else gemini_fallback_raw
        if not gemini_fallback_clean or gemini_fallback_clean == "gemini-3.8-flash":
            gemini_fallback_clean = "gemini-flash-latest"

        if primary_provider == "gemini":
            # Active runtime cascade: Gemini Flash-Lite primary -> Gemini Flash fallback
            # Groq is intentionally excluded from the active runtime cascade.
            cascade_tiers = [
                {
                    "provider": "gemini",
                    "model": gemini_primary_clean,
                    "display_name": "Gemini",
                    "model_display": gemini_primary_clean,
                    "is_fallback": False,
                    "max_retries": self.gemini_max_retries,
                },
                {
                    "provider": "gemini",
                    "model": gemini_fallback_clean,
                    "display_name": "Gemini",
                    "model_display": gemini_fallback_clean,
                    "is_fallback": True,
                    "max_retries": self.gemini_max_retries,
                },
            ]
        else:
            groq_model = (
                getattr(settings, "GROQ_MODEL", "")
                or getattr(settings, "CREWAI_MODEL", "")
                or "openai/gpt-oss-120b"
            )
            cascade_tiers = [
                {
                    "provider": "groq",
                    "model": groq_model,
                    "display_name": "Groq",
                    "model_display": groq_model,
                    "is_fallback": False,
                    "max_retries": 1,
                },
                {
                    "provider": "gemini",
                    "model": gemini_primary_clean,
                    "display_name": "Gemini",
                    "model_display": gemini_primary_clean,
                    "is_fallback": True,
                    "max_retries": self.gemini_max_retries,
                },
            ]

        tier_failures = []
        overall_start = time.time()
        overall_deadline = overall_start + self.overall_timeout
        logger.info(
            f"[LLM Timing] App #{application_id}: Starting verification cascade across {len(cascade_tiers)} tiers "
            f"(overall deadline: {self.overall_timeout:.1f}s)"
        )

        for tier_idx, tier in enumerate(cascade_tiers, 1):
            tier_name = tier["display_name"]
            model_name = tier["model"]
            model_display = tier["model_display"]
            max_attempts = tier["max_retries"]

            # Abort event / Deadline check before tier start
            if abort_event.is_set():
                elapsed = time.time() - overall_start
                logger.warning(
                    f"[LLM Timing] App #{application_id}: Abort event signaled after {elapsed:.2f}s "
                    f"before starting Tier {tier_idx} ({tier_name}/{model_display})."
                )
                raise TimeoutError(
                    f"Multi-Agent verification aborted: overall workflow deadline or cancellation signaled."
                )

            if time.time() >= overall_deadline:
                elapsed = time.time() - overall_start
                logger.error(
                    f"[LLM Timing] App #{application_id}: Workflow deadline ({self.overall_timeout}s) exceeded "
                    f"after {elapsed:.2f}s before starting Tier {tier_idx} ({tier_name}/{model_display})."
                )
                raise TimeoutError(
                    f"Multi-Agent verification exceeded overall workflow deadline of {self.overall_timeout}s."
                )

            if not tier["is_fallback"]:
                logger.info(f"[LLM] Primary provider: {tier_name} (Tier {tier_idx})")
            else:
                logger.warning(f"[LLM] Falling back to {tier_name} ({model_display}) (Tier {tier_idx})")
                logger.info(f"[LLM] Fallback provider: {tier_name}")
                logger.info(f"[LLM] {tier_name} verification started (model: {model_display})")

            abort_provider_cascade = False

            for attempt in range(1, max_attempts + 1):
                # Abort event / Deadline check before attempt start
                if abort_event.is_set():
                    elapsed = time.time() - overall_start
                    logger.warning(
                        f"[LLM Timing] App #{application_id}: Abort event signaled after {elapsed:.2f}s "
                        f"before Tier {tier_idx} attempt {attempt}."
                    )
                    raise TimeoutError(
                        f"Multi-Agent verification aborted: overall workflow deadline or cancellation signaled."
                    )

                remaining_time = overall_deadline - time.time()
                if remaining_time <= 0:
                    elapsed = time.time() - overall_start
                    logger.error(
                        f"[LLM Timing] App #{application_id}: Workflow deadline ({self.overall_timeout}s) exceeded "
                        f"after {elapsed:.2f}s before Tier {tier_idx} attempt {attempt}."
                    )
                    raise TimeoutError(
                        f"Multi-Agent verification exceeded overall workflow deadline of {self.overall_timeout}s."
                    )

                tier_attempt_start = time.time()
                tier_timeout_raw = getattr(settings, "LLM_TIER_TIMEOUT_SECONDS", 175.0)
                try:
                    tier_timeout_cap = float(tier_timeout_raw)
                except (TypeError, ValueError):
                    tier_timeout_cap = 175.0

                MIN_FALLBACK_BUDGET = 50.0
                if remaining_time < MIN_FALLBACK_BUDGET:
                    logger.warning(
                        f"[LLM Timing] App #{application_id}: Insufficient budget remaining ({remaining_time:.1f}s < {MIN_FALLBACK_BUDGET}s) "
                        f"for Tier {tier_idx} ({tier_name}/{model_display}). Aborting cascade."
                    )
                    raise TimeoutError(
                        f"Multi-Agent verification exceeded remaining workflow deadline ({remaining_time:.1f}s remaining)."
                    )

                has_subsequent_tier = (tier_idx < len(cascade_tiers))
                if has_subsequent_tier and remaining_time > (tier_timeout_cap + MIN_FALLBACK_BUDGET):
                    attempt_timeout = min(tier_timeout_cap, remaining_time - MIN_FALLBACK_BUDGET)
                elif has_subsequent_tier and remaining_time > MIN_FALLBACK_BUDGET:
                    attempt_timeout = remaining_time - MIN_FALLBACK_BUDGET
                else:
                    attempt_timeout = remaining_time

                logger.info(
                    f"[LLM Timing] App #{application_id}: Tier {tier_idx} ({tier_name}/{model_display}) "
                    f"attempt {attempt}/{max_attempts} started (timeout={attempt_timeout:.1f}s, budget remaining={remaining_time:.1f}s)"
                )
                try:
                    import concurrent.futures
                    attempt_abort_event = threading.Event()
                    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                    try:
                        future = executor.submit(
                            self._execute_pipeline,
                            provider_name=tier["provider"],
                            model_name=tier["model"],
                            application_id=application_id,
                            applicant_name=applicant_name,
                            loan_amount=loan_amount,
                            documents=documents,
                            loan_type=loan_type,
                            loan_tenure=loan_tenure,
                            gov_verification_record=gov_verification_record,
                            abort_event=abort_event,
                            overall_deadline=overall_deadline,
                            attempt_abort_event=attempt_abort_event,
                        )
                        try:
                            res = future.result(timeout=attempt_timeout)
                            executor.shutdown(wait=False)
                        except concurrent.futures.TimeoutError:
                            attempt_abort_event.set()
                            executor.shutdown(wait=False, cancel_futures=True)
                            elapsed = time.time() - overall_start
                            if time.time() >= overall_deadline:
                                abort_event.set()
                                logger.error(
                                    f"[LLM Timing] App #{application_id}: Workflow deadline ({self.overall_timeout}s) exceeded "
                                    f"during Tier {tier_idx} attempt {attempt}."
                                )
                                raise TimeoutError(
                                    f"Multi-Agent verification exceeded overall workflow deadline of {self.overall_timeout}s."
                                )
                            else:
                                logger.warning(
                                    f"[LLM Timing] App #{application_id}: Tier {tier_idx} ({tier_name}/{model_display}) "
                                    f"attempt {attempt} timed out after {attempt_timeout:.1f}s. Signaling cooperative abort..."
                                )
                                try:
                                    future.result(timeout=2.0)
                                    worker_halted = True
                                except (concurrent.futures.TimeoutError, Exception):
                                    worker_halted = future.done()

                                if not worker_halted and has_subsequent_tier:
                                    logger.warning(
                                        f"[LLM Timing] App #{application_id}: Tier {tier_idx} worker thread did not halt within grace period. "
                                        f"Suppressing concurrent fallback to avoid rate-limit collision and resource contention."
                                    )
                                    tier_failures.append(f"{tier_name} ({model_display}): timed out and thread still active")
                                    raise TimeoutError(
                                        f"Multi-Agent verification timed out during Tier {tier_idx} ({attempt_timeout:.1f}s). "
                                        f"Active attempt suppressed concurrent fallback."
                                    )

                                logger.warning(
                                    f"[LLM Timing] App #{application_id}: Tier {tier_idx} ({tier_name}/{model_display}) "
                                    f"halted. Preserving overall workflow ({overall_deadline - time.time():.1f}s remaining) for fallback cascade."
                                )
                                tier_failures.append(f"{tier_name} ({model_display}): timed out after {attempt_timeout:.1f}s")
                                break
                        except Exception:
                            executor.shutdown(wait=False)
                            raise
                    except Exception:
                        executor.shutdown(wait=False)
                        raise

                    elapsed = time.time() - tier_attempt_start
                    if time.time() >= overall_deadline:
                        raise TimeoutError(
                            f"Multi-Agent verification exceeded overall workflow deadline of {self.overall_timeout}s."
                        )

                    logger.info(
                        f"[LLM Timing] App #{application_id}: Tier {tier_idx} ({tier_name}/{model_display}) "
                        f"attempt {attempt} succeeded in {elapsed:.2f}s (total elapsed {time.time() - overall_start:.2f}s)"
                    )
                    return res
                except Exception as exc:
                    if "suppressed concurrent fallback" in str(exc).lower():
                        raise
                    elapsed = time.time() - tier_attempt_start
                    classification = classify_provider_error(exc)
                    category = classification["category"]
                    is_retryable = classification["is_retryable"]
                    sanitized_reason = classification["sanitized_reason"]
                    is_prov, _ = is_provider_error(exc)

                    # 1. Billing errors: Never retry and abort cascade to same provider
                    if category == "billing_error":
                        logger.error(
                            f"[LLM Timing] App #{application_id}: Non-retryable billing error on "
                            f"{tier_name} ({model_display}): {sanitized_reason}. Aborting retries."
                        )
                        tier_failures.append(f"{tier_name} ({model_display}): {sanitized_reason}")
                        abort_provider_cascade = True
                        break

                    # 2. Daily Quota Exhaustion: Never retry on same provider
                    if category == "daily_quota_exhausted":
                        logger.error(
                            f"[LLM Timing] App #{application_id}: Daily free-tier quota exhausted on "
                            f"{tier_name} ({model_display}): {sanitized_reason}. Aborting retries."
                        )
                        tier_failures.append(f"{tier_name} ({model_display}): {sanitized_reason}")
                        abort_provider_cascade = True
                        break

                    # 3. Retryable transient failures (429 rate limit, 5xx, timeout)
                    if is_retryable:
                        logger.warning(
                            f"[LLM Timing] App #{application_id}: Tier {tier_idx} ({tier_name}/{model_display}) "
                            f"attempt {attempt} failed after {elapsed:.2f}s: {sanitized_reason}"
                        )
                        remaining_budget = overall_deadline - time.time()
                        # If a fallback tier exists, ensure we have enough budget left for fallback after retrying.
                        # If remaining_budget is insufficient for another full attempt plus fallback, cascade immediately.
                        min_needed_to_retry = max(elapsed, 45.0) + MIN_FALLBACK_BUDGET if has_subsequent_tier else 10.0
                        should_retry = (
                            attempt < max_attempts and
                            remaining_budget >= min_needed_to_retry and
                            remaining_budget > 4.0
                        )
                        if should_retry:
                            delay = min(self.backoff_base * (2 ** (attempt - 1)), 3.0)
                            logger.info(
                                f"[LLM] Retrying {tier_name} ({model_display}) in {delay:.2f}s "
                                f"(bounded backoff, attempt {attempt + 1}/{max_attempts})..."
                            )
                            time.sleep(delay)
                            continue
                        else:
                            if has_subsequent_tier and attempt < max_attempts:
                                logger.warning(
                                    f"[LLM Cascade] App #{application_id}: Remaining budget ({remaining_budget:.1f}s) "
                                    f"insufficient for retry + fallback. Cascading immediately to fallback tier."
                                )
                            tier_failures.append(f"{tier_name} ({model_display}): {sanitized_reason}")
                            break
                    elif is_prov:
                        # Non-retryable provider error (e.g. 401 auth, 404 model)
                        logger.warning(
                            f"[LLM Timing] App #{application_id}: Tier {tier_idx} ({tier_name}/{model_display}) "
                            f"failed with non-retryable provider error after {elapsed:.2f}s: {sanitized_reason}. "
                            f"Skipping immediate retries on this provider and proceeding to fallback."
                        )
                        tier_failures.append(f"{tier_name} ({model_display}): {sanitized_reason}")
                        break
                    else:
                        logger.error(
                            f"[LLM Timing] App #{application_id}: Non-provider application error in "
                            f"{tier_name} pipeline after {elapsed:.2f}s: {sanitized_reason}"
                        )
                        raise

            if abort_provider_cascade:
                # If a billing error or daily quota exhaustion occurred on a provider,
                # trying another model tier on the exact same provider account will fail identically.
                current_prov = tier["provider"]
                remaining_tiers = [t for t in cascade_tiers[tier_idx:] if t["provider"] != current_prov]
                if not remaining_tiers:
                    logger.error(
                        f"[LLM Timing] App #{application_id}: All remaining tiers use provider '{current_prov}' "
                        f"which has unrecoverable billing/quota issues. Halting cascade."
                    )
                    break

        failure_details = " -> ".join(tier_failures)
        logger.error(
            f"[LLM Timing] App #{application_id}: All configured providers in fallback cascade failed "
            f"after {time.time() - overall_start:.2f}s: {failure_details}"
        )
        raise RuntimeError(
            f"Multi-Agent verification failed: All configured providers in fallback cascade failed. "
            f"Details: {failure_details}"
        )

    def _execute_pipeline(
        self,
        provider_name: str,
        model_name: str,
        application_id: int,
        applicant_name: str,
        loan_amount: float,
        documents: List[Dict[str, Any]],
        loan_type: str = "Home Loan",
        loan_tenure: int = 60,
        gov_verification_record: Dict[str, Any] = None,
        abort_event: Optional[threading.Event] = None,
        overall_deadline: Optional[float] = None,
        attempt_abort_event: Optional[threading.Event] = None,
    ) -> Dict[str, Any]:
        """
        Execute the 5-agent CrewAI pipeline with the specified LLM provider and model.
        """
        import time
        from app.agents.context import SharedAgentContext

        # Phase 4: Shared Context
        shared_context = SharedAgentContext(
            application_id=application_id,
            applicant_name=applicant_name,
            loan_amount=loan_amount
        )

        start_time = time.time()

        agents = build_agents(
            self.db,
            shared_context=shared_context,
            provider=provider_name,
            model=model_name,
        )
        tasks = build_tasks(
            agents,
            run_context={
                "application_id": application_id,
                "applicant_name": applicant_name,
                "loan_amount": loan_amount,
                "loan_type": loan_type,
                "loan_tenure": loan_tenure,
                "documents": documents,
                "gov_verification_record": gov_verification_record or {},
                "shared_context_summary": shared_context.get_summary_context()
            },
        )

        stage_start = [start_time]

        def _on_task_complete(output):
            now = time.time()
            stage_elapsed = now - stage_start[0]
            stage_start[0] = now
            agent_role = getattr(output, "agent", "Agent")
            logger.info(
                f"[Agent Timing] App #{application_id}: Stage completed by [{agent_role}] "
                f"in {stage_elapsed:.2f}s (pipeline elapsed: {now - start_time:.2f}s)"
            )
            if abort_event and abort_event.is_set():
                logger.warning(
                    f"[Pipeline Abort] App #{application_id}: Abort event signaled after [{agent_role}]. Halting remaining agents."
                )
                raise TimeoutError("Verification aborted: overall workflow deadline exceeded.")
            if attempt_abort_event and attempt_abort_event.is_set():
                logger.warning(
                    f"[Pipeline Abort] App #{application_id}: Attempt abort event signaled after [{agent_role}]. Halting remaining agents."
                )
                raise TimeoutError("Verification aborted: tier attempt timed out.")
            if overall_deadline and time.time() >= overall_deadline:
                logger.warning(
                    f"[Pipeline Abort] App #{application_id}: Deadline exceeded after [{agent_role}]. Halting remaining agents."
                )
                raise TimeoutError("Verification aborted: overall workflow deadline exceeded.")

        crew = Crew(
            agents=list(agents.values()),
            tasks=tasks,
            process=Process.sequential,
            verbose=True,
            task_callback=_on_task_complete,
        )

        # Abort event / deadline check before crew kickoff
        if abort_event and abort_event.is_set():
            logger.warning(
                f"[Pipeline Abort] App #{application_id}: Abort event signaled before crew kickoff. Halting execution."
            )
            raise TimeoutError("Verification aborted: overall workflow deadline exceeded.")
        if attempt_abort_event and attempt_abort_event.is_set():
            logger.warning(
                f"[Pipeline Abort] App #{application_id}: Attempt abort event signaled before crew kickoff. Halting execution."
            )
            raise TimeoutError("Verification aborted: tier attempt timed out.")
        if overall_deadline and time.time() >= overall_deadline:
            logger.warning(
                f"[Pipeline Abort] App #{application_id}: Deadline exceeded before crew kickoff. Halting execution."
            )
            raise TimeoutError("Verification aborted: overall workflow deadline exceeded.")

        # Crew AI execution (Phase 8 Timeline tracking)
        shared_context.record_timing("crewai_start", time.time())
        logger.info(f"[Pipeline Timing] App #{application_id}: CrewAI 5-agent kickoff started (provider={provider_name}, model={model_name})")
        crew_output = crew.kickoff()
        elapsed_crewai = time.time() - start_time
        shared_context.record_timing("crewai", elapsed_crewai)
        logger.info(f"[Pipeline Timing] App #{application_id}: CrewAI 5-agent kickoff completed in {elapsed_crewai:.2f}s")

        raw_output = self._extract_raw(crew_output)
        if hasattr(crew_output, "json_dict") and isinstance(crew_output.json_dict, dict) and crew_output.json_dict:
            result = dict(crew_output.json_dict)
        elif hasattr(crew_output, "pydantic") and crew_output.pydantic:
            result = crew_output.pydantic.model_dump()
        else:
            result = self._parse_final_json(raw_output)

        if not result or not isinstance(result, dict) or not any(k in result for k in ("verification_score", "final_status", "extracted_info", "verification_details")):
            raise ValueError(f"Malformed LLM response from {provider_name}: unable to extract structured verification findings from model output.")

        result["agent_trace"] = raw_output

        # Defensive defaults in case the LLM omitted a field
        result.setdefault("final_status", "manual_review")
        result.setdefault("verification_score", 0.0)
        result.setdefault("risk_score", 0.0)
        result.setdefault("fraud_flag", False)
        result.setdefault("extracted_info", {})
        result.setdefault("verification_details", {})
        result.setdefault("fraud_analysis", {})
        result.setdefault("pdf_path", None)
        result.setdefault("summary", "")
        result.setdefault("agent_findings", {})
        result.setdefault("recommendation", "")
        result.setdefault("human_review", "")

        # New Phase 6 & 7 & 8 fields
        result.setdefault("explainable_ai", {})
        result.setdefault("confidence_score", 0.0)
        result["agent_memory"] = shared_context.agent_memory

        shared_context.record_timing("total", time.time() - start_time)
        result["execution_timeline"] = shared_context.execution_timeline
        result["llm_provider"] = provider_name
        result["llm_model"] = model_name

        # Safety net: fraud flag always forces rejection, even if the
        # compliance reporter agent got this wrong.
        if result.get("fraud_flag") and result["final_status"] != "rejected":
            logger.warning(
                "Crew set final_status=%s despite fraud_flag=True; forcing 'rejected'.",
                result["final_status"],
            )
            result["final_status"] = "rejected"

        return result

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_raw(crew_output: Any) -> str:
        """Normalise CrewOutput (or plain string) into a raw string."""
        if hasattr(crew_output, "raw"):
            return crew_output.raw
        return str(crew_output)

    @staticmethod
    def _parse_final_json(raw_output: str) -> Dict[str, Any]:
        """
        Parse the compliance reporter's final JSON object out of the raw
        crew output, tolerating markdown code fences or surrounding prose.
        """
        if not raw_output:
            return {}

        text = raw_output.strip()

        # Strip ```json ... ``` or ``` ... ``` fences if present
        fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
        if fence_match:
            text = fence_match.group(1)

        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Fallback: find the last top-level-looking {...} block
        brace_match = re.search(r"(\{.*\})", text, re.DOTALL)
        if brace_match:
            candidate = brace_match.group(1)
            try:
                return json.loads(candidate)
            except json.JSONDecodeError as e:
                logger.error(
                    f"[CrewAI Output Parse Error] Failed to parse candidate JSON: {e.msg} "
                    f"at line {e.lineno} col {e.colno} (length={len(candidate)})"
                )

        logger.error(
            f"[CrewAI Output Error] Could not extract valid JSON structure from crew output "
            f"(output_length={len(text)}, has_braces={bool(brace_match)})"
        )
        return {}
