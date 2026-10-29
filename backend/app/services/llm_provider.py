"""
LLM Provider Abstraction for SmartVerify Report Generation System (Phase 6).
Provides a clean interface separating business logic from LLM APIs.
Supports Google Gemini via REST API and a Mock provider for 100% deterministic,
offline automated testing.
"""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, Set
import json
import logging
import os

from app.core.config import settings

logger = logging.getLogger(__name__)


class ProviderError(Exception):
    """Raised when an LLM provider fails (timeout, network error, invalid response)."""
    pass


class LLMProvider(ABC):
    """Abstract interface for LLM text and structured JSON generation."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Returns the model identifier string."""
        pass

    @abstractmethod
    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        json_mode: bool = True,
    ) -> str:
        """
        Executes generation and returns the raw response string (typically JSON).
        Raises ProviderError on failure.
        """
        pass


class GeminiRESTProvider(LLMProvider):
    """
    Direct Google Gemini REST API client.
    Reuses existing environment configuration without third-party LLM orchestrator overhead.
    Does not log or expose API keys.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_seconds: float = 30.0,
    ):
        raw_model = model or getattr(settings, "CREWAI_MODEL", "gemini-2.5-flash")
        # Strip provider prefix if present (e.g. 'gemini/gemini-2.5-flash' -> 'gemini-2.5-flash')
        if "/" in raw_model:
            raw_model = raw_model.split("/")[-1]
        self._model = raw_model

        self._api_key = (
            api_key
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or getattr(settings, "GEMINI_API_KEY", "")
            or getattr(settings, "GOOGLE_API_KEY", "")
            or getattr(settings, "gemini_api_key", "")
            or ""
        )
        self.timeout_seconds = timeout_seconds

    @property
    def model_name(self) -> str:
        return self._model

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        json_mode: bool = True,
    ) -> str:
        if not self._api_key:
            raise ProviderError("Gemini API key is not configured in settings or environment.")

        import requests

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent"
        headers = {"Content-Type": "application/json"}
        params = {"key": self._api_key}

        contents = []
        if prompt:
            contents.append({"role": "user", "parts": [{"text": prompt}]})

        body: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": 0.1,
            },
        }

        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"

        if system_prompt:
            body["systemInstruction"] = {
                "parts": [{"text": system_prompt}]
            }

        try:
            resp = requests.post(
                url,
                headers=headers,
                params=params,
                json=body,
                timeout=self.timeout_seconds,
            )
            if resp.status_code != 200:
                raise ProviderError(f"Gemini API returned status {resp.status_code}: {resp.text[:200]}")

            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                raise ProviderError("Gemini API returned zero candidates.")

            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                raise ProviderError("Gemini candidate content has no text parts.")

            return parts[0].get("text", "")

        except requests.RequestException as e:
            raise ProviderError(f"Network error during Gemini API call: {e}") from e
        except Exception as e:
            raise ProviderError(f"Unexpected error during Gemini API call: {e}") from e


class MockLLMProvider(LLMProvider):
    """
    Deterministic Mock LLM Provider for unit and regression testing.
    Zero external network calls, zero API costs, and 100% predictable responses.
    Allows simulating edge cases: provider failures, malformed JSON, timeouts.
    """

    def __init__(
        self,
        model_name: str = "mock-gemini-2.5-flash",
        custom_responses: Optional[Dict[str, str]] = None,
        fail_particular_ids: Optional[Set[str]] = None,
        malformed_particular_ids: Optional[Set[str]] = None,
    ):
        self._model_name = model_name
        self.custom_responses: Dict[str, str] = custom_responses or {}
        self.fail_particular_ids: Set[str] = fail_particular_ids or set()
        self.malformed_particular_ids: Set[str] = malformed_particular_ids or set()
        self.call_history: list = []

    @property
    def model_name(self) -> str:
        return self._model_name

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        json_mode: bool = True,
    ) -> str:
        self.call_history.append({"prompt": prompt, "system_prompt": system_prompt})

        # Check if a specific particular ID is in prompt and configured to fail
        for pid in self.fail_particular_ids:
            if f'"particular_id": "{pid}"' in prompt or f"Particular ID: {pid}" in prompt or f"'{pid}'" in prompt:
                raise ProviderError(f"Simulated provider failure for particular {pid}")

        for pid in self.malformed_particular_ids:
            if f'"particular_id": "{pid}"' in prompt or f"Particular ID: {pid}" in prompt or f"'{pid}'" in prompt:
                return "{ malformed json: not valid json at all ... "

        # Check custom responses
        for key, resp in self.custom_responses.items():
            if key in prompt:
                return resp

        # Default structured fallback matching ComposedParticular contract
        default_payload = {
            "text": "Verification observations confirmed from submitted documents and verified findings.",
            "fact_ids": [],
            "evidence_ids": [],
            "confidence": 0.95,
            "missing_information": [],
        }
        return json.dumps(default_payload)


def mask_secrets(text: Any) -> str:
    """
    Sanitize text by redacting API keys, sensitive tokens, and applicant personal information.
    Prevents leaking credentials or applicant PII in logs, console output, or error messages.
    """
    if text is None:
        return ""
    s = str(text)

    # 1. Redact exact known secrets from settings & env if configured
    known_keys = set()
    for attr in ("GROQ_API_KEY", "groq_api_key", "GEMINI_API_KEY", "gemini_api_key", "GOOGLE_API_KEY", "SECRET_KEY"):
        val = getattr(settings, attr, None) or os.environ.get(attr)
        if val and isinstance(val, str) and len(val.strip()) >= 8:
            known_keys.add(val.strip())

    for key in known_keys:
        if key in s:
            s = s.replace(key, "[REDACTED_API_KEY]")

    # 2. Redact patterns: Groq (gsk_...), Google (AIza... or AQ....), Bearer tokens, query keys
    import re
    s = re.sub(r"gsk_[A-Za-z0-9_-]{10,}", "[REDACTED_GROQ_KEY]", s)
    s = re.sub(r"AIza[0-9A-Za-z_-]{25,}", "[REDACTED_GEMINI_KEY]", s)
    s = re.sub(r"AQ\.[A-Za-z0-9_-]{20,}", "[REDACTED_GEMINI_KEY]", s)
    s = re.sub(r"(Bearer\s+)[A-Za-z0-9._-]{15,}", r"\1[REDACTED_TOKEN]", s)
    s = re.sub(r"([?&]key=)[^&\s]+", r"\1[REDACTED_KEY]", s)

    # 3. Redact applicant personal information (PII)
    s = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[REDACTED_EMAIL]", s)
    s = re.sub(r"\b[A-Z]{5}[0-9]{4}[A-Z]{1}\b", "[REDACTED_PAN]", s)
    s = re.sub(r"\b\d{4}\s\d{4}\s\d{4}\b", "[REDACTED_AADHAAR]", s)
    s = re.sub(r"\b\d{12}\b", "[REDACTED_AADHAAR]", s)
    s = re.sub(r"\b(?:\+91[\s-]?)?[6-9]\d{9}\b", "[REDACTED_PHONE]", s)

    return s


def classify_provider_error(exc: Exception) -> dict:
    """
    Classify an exception into a definitive provider error category:
    - billing_error: Non-retryable. Project billing inactive or required.
    - auth_error: Non-retryable. Bad API key or unauthorized permissions.
    - daily_quota_exhausted: Non-retryable. Daily free-tier or plan cap exhausted.
    - rate_limit: Retryable once with backoff. Transient RPM/TPM spike.
    - server_error: Retryable once with backoff. 5xx or transient provider overload.
    - timeout: Retryable once with backoff. Connection or read timeout.
    - client_error: Non-retryable. Model not found, bad request, or invalid parameters.
    - application_error: Non-retryable internal error.
    """
    if exc is None:
        return {"category": "none", "is_retryable": False, "sanitized_reason": "No exception"}

    err_str = f"{type(exc).__name__}: {str(exc)}"
    if getattr(exc, "__cause__", None):
        err_str += f" | Cause: {type(exc.__cause__).__name__}: {str(exc.__cause__)}"

    sanitized = mask_secrets(err_str)
    lower = sanitized.lower()
    exc_cls_name = type(exc).__name__

    status_code = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    if status_code is None and hasattr(exc, "response") and hasattr(exc.response, "status_code"):
        status_code = exc.response.status_code

    # 1. Billing errors (Priority: must be checked before quota/rate limit)
    billing_keywords = (
        "billing not enabled", "billing is not enabled", "enable billing",
        "billing account", "has not enabled billing", "billing disabled",
        "billing required", "payment required", "no active billing"
    )
    if status_code == 402 or any(k in lower for k in billing_keywords):
        return {
            "category": "billing_error",
            "is_retryable": False,
            "can_fallback": False,
            "sanitized_reason": f"billing error: {sanitized}"
        }

    # 2. Authentication / Permission errors (HTTP 401, 403, or auth messages)
    auth_keywords = (
        "api_key_invalid", "invalid_api_key", "invalid api key", "unauthorized",
        "authentication failed", "permission denied", "forbidden", "bad credentials",
        "unauthenticated", "api key not valid"
    )
    if status_code in (401, 403) or any(k in exc_cls_name for k in ("Authentication", "PermissionDenied", "Unauthorized", "Unauthenticated")) or any(k in lower for k in auth_keywords):
        return {
            "category": "auth_error",
            "is_retryable": False,
            "can_fallback": True,
            "sanitized_reason": f"authentication error (HTTP {status_code or exc_cls_name}): {sanitized}"
        }

    # 3. Client / Model errors (HTTP 400, 404, or invalid model messages)
    client_keywords = (
        "model not found", "is no longer available", "does not exist",
        "unsupported model", "invalid_argument", "tool_use_failed"
    )
    if status_code in (400, 404) or any(k in exc_cls_name for k in ("NotFound", "NotFoundError", "UnmappedModel", "BadRequest", "BadRequestError")) or any(k in lower for k in client_keywords):
        return {
            "category": "client_error",
            "is_retryable": False,
            "can_fallback": True,
            "sanitized_reason": f"client/model error (HTTP {status_code or exc_cls_name}): {sanitized}"
        }

    # 4. Daily / Account Quota Exhaustion (Distinguished from transient rate limits)
    daily_quota_keywords = (
        "daily quota", "free_tier_requests_per_day", "requests per day", "rpd",
        "quota exceeded for quota metric ... per day", "quota exhausted for the day",
        "day limit"
    )
    if any(k in lower for k in daily_quota_keywords):
        return {
            "category": "daily_quota_exhausted",
            "is_retryable": False,
            "can_fallback": False,
            "sanitized_reason": f"daily quota exhausted: {sanitized}"
        }

    # 5. Non-retryable application/parsing errors
    non_retryable_types = (
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        json.JSONDecodeError,
    )
    try:
        from pydantic import ValidationError as PydanticValidationError
        non_retryable_types = non_retryable_types + (PydanticValidationError,)
    except ImportError:
        pass

    if isinstance(exc, non_retryable_types) and not any(k in lower for k in ("429", "500", "502", "503", "504", "rate limit", "timeout")):
        return {
            "category": "application_error",
            "is_retryable": False,
            "can_fallback": False,
            "sanitized_reason": sanitized
        }

    # 6. Transient Rate Limits (HTTP 429, RPM/TPM limit exceeded)
    rate_limit_keywords = (
        "rate limit", "rate_limit", "quota exceeded", "quota", "429",
        "resource_exhausted", "too many requests", "tokens per minute", "tpm", "rpm"
    )
    if status_code == 429 or any(k in exc_cls_name for k in ("RateLimitError", "QuotaExceeded", "ResourceExhausted")) or any(k in lower for k in rate_limit_keywords):
        return {
            "category": "rate_limit",
            "is_retryable": True,
            "can_fallback": True,
            "sanitized_reason": f"429 rate limit: {sanitized}"
        }

    # 7. Temporary 5xx Server Errors
    server_keywords = (
        "500 internal server error", "502 bad gateway", "503 service unavailable",
        "503", "504 gateway timeout", "504", "overloaded", "high demand",
        "temporarily unavailable", "server is busy", "capacity", "unavailable"
    )
    if status_code in (500, 502, 503, 504) or any(k in exc_cls_name for k in ("ServiceUnavailableError", "InternalServerError", "BadGatewayError")) or any(k in lower for k in server_keywords):
        return {
            "category": "server_error",
            "is_retryable": True,
            "can_fallback": True,
            "sanitized_reason": f"{status_code or '5xx'} provider error: {sanitized}"
        }

    # 8. Timeout & Connection Failures
    timeout_keywords = (
        "timeout", "timed out", "connection reset", "connection refused",
        "failed to connect", "network unreachable"
    )
    if any(k in exc_cls_name for k in ("Timeout", "TimeOut", "ConnectTimeout", "ReadTimeout", "APIConnectionError", "ConnectError", "NetworkError", "ConnectionRefusedError")) or any(k in lower for k in timeout_keywords):
        return {
            "category": "timeout",
            "is_retryable": True,
            "can_fallback": True,
            "sanitized_reason": f"timeout / connection failure: {sanitized}"
        }

    return {
        "category": "unknown",
        "is_retryable": False,
        "can_fallback": False,
        "sanitized_reason": sanitized
    }


def is_retryable_provider_error(exc: Exception) -> tuple[bool, str]:
    """
    Determine if an exception represents a transient provider failure:
    - HTTP 429 / transient rate limit
    - HTTP 5xx temporary server errors (500, 502, 503, 504)
    - Timeout / network connection failure

    Returns:
        (is_retryable, sanitized_reason_str)

    Explicitly excludes (NON-RETRYABLE):
    - Billing failures (billing not enabled, billing account missing)
    - Daily quota exhaustion (requests per day reached)
    - Authentication errors (HTTP 401, 403, auth messages)
    - Client/model errors (HTTP 400, 404, invalid models)
    - Application errors (ValueError, KeyError, TypeError, JSONDecodeError)
    """
    classified = classify_provider_error(exc)
    return classified["is_retryable"], classified["sanitized_reason"]


def is_provider_error(exc: Exception) -> tuple[bool, str]:
    """
    Determine if an exception represents a provider-level failure eligible for
    fallback cascade (as opposed to an internal application bug like TypeError/KeyError):
    - HTTP 401 / 403 (Authentication / Authorization / Permission failure)
    - HTTP 404 (Model not found / unmapped / unavailable)
    - HTTP 400 (Bad request / invalid model parameters)
    - HTTP 429 / 5xx (Rate limit / Quota / Gateway / Server error)
    - Timeout / Network connection failure
    - LiteLLM / Provider SDK exceptions (APIError, BadRequestError, AuthenticationError, etc.)
    - Malformed LLM output errors
    """
    if exc is None:
        return False, "No exception"

    err_str = f"{type(exc).__name__}: {str(exc)}"
    if getattr(exc, "__cause__", None):
        err_str += f" | Cause: {type(exc.__cause__).__name__}: {str(exc.__cause__)}"

    sanitized = mask_secrets(err_str)
    lower = sanitized.lower()

    if isinstance(exc, ProviderError):
        return True, sanitized

    status_code = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    if status_code is None and hasattr(exc, "response") and hasattr(exc.response, "status_code"):
        status_code = exc.response.status_code

    if status_code in (400, 401, 403, 404, 429, 500, 502, 503, 504):
        return True, sanitized

    exc_cls = type(exc).__name__
    if any(k in exc_cls for k in (
        "RateLimit", "Quota", "Auth", "NotFound", "BadRequest",
        "APIError", "APIConnection", "Timeout", "ServiceUnavailable",
        "InternalServer", "PermissionDenied", "UnmappedModel",
        "ResourceExhausted", "GoogleAPICallError"
    )):
        return True, sanitized

    provider_keywords = (
        "api_key", "invalid_api_key", "unauthorized", "authentication",
        "permission", "forbidden", "rate limit", "rate_limit", "quota",
        "too many requests", "429", "401", "403", "404", "500", "502", "503", "504",
        "model not found", "does not exist", "unsupported model", "is not mapped",
        "timeout", "timed out", "connection", "groq", "gemini", "openai",
        "litellm", "resource_exhausted", "malformed llm response", "billing"
    )
    if any(k in lower for k in provider_keywords):
        return True, sanitized

    non_provider_types = (
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        IndexError,
        ImportError,
    )
    if isinstance(exc, non_provider_types):
        return False, sanitized

    return False, sanitized

