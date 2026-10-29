"""
SmartVerify Storage Service
Unified storage abstraction supporting local filesystem and Supabase Storage.
Provides opaque key generation, private bucket interactions, temporary file
lifecycle management for OCR, and short-lived signed URLs.
"""

from abc import ABC, abstractmethod
from contextlib import contextmanager
import logging
import mimetypes
import os
from pathlib import Path
import tempfile
import time
from typing import BinaryIO, Iterator, Optional, Union
import uuid

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Custom Storage Exceptions
# ─────────────────────────────────────────────────────────────────────────────

class StorageError(Exception):
    """Base exception for storage operations."""
    pass


class StorageConfigurationError(StorageError):
    """Raised when storage configuration is missing or invalid."""
    pass


class StorageFileNotFoundError(StorageError):
    """Raised when the requested object is not found in storage."""
    pass


class StorageUploadError(StorageError):
    """Raised when uploading an object to storage fails."""
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Opaque Key Generation (Zero PII)
# ─────────────────────────────────────────────────────────────────────────────

ALLOWED_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"
}


def generate_object_key(category: str, application_id: int, original_filename: str) -> str:
    """
    Generate an opaque, collision-resistant object key without PII.

    Never includes applicant names, Aadhaar numbers, PAN numbers, or user IDs.
    Format: applications/{application_id}/{category}/{uuid4_hex}{ext}
    """
    clean_category = category.strip().strip("/").lower()
    if clean_category not in {"documents", "gov_screenshots", "reports"}:
        clean_category = "documents"

    raw_ext = Path(original_filename or "").suffix.lower()
    ext = raw_ext if raw_ext in ALLOWED_EXTENSIONS else (".pdf" if clean_category == "reports" else ".bin")
    unique_id = uuid.uuid4().hex

    return f"applications/{int(application_id)}/{clean_category}/{unique_id}{ext}"


# ─────────────────────────────────────────────────────────────────────────────
# Base Storage Interface
# ─────────────────────────────────────────────────────────────────────────────

class BaseStorageService(ABC):
    """Abstract storage interface implemented by Local and Supabase providers."""

    @abstractmethod
    def upload_file(
        self,
        bucket: str,
        object_key: str,
        data: Union[bytes, BinaryIO],
        content_type: Optional[str] = None,
    ) -> str:
        """Upload raw bytes or file-like object to storage and return the object key."""
        pass

    @abstractmethod
    def download_file(self, bucket: str, object_key: str) -> bytes:
        """Download raw object bytes from storage."""
        pass

    @abstractmethod
    def delete_file(self, bucket: str, object_key: str) -> bool:
        """Delete an object from storage. Returns True if removed or already absent."""
        pass

    @abstractmethod
    def create_signed_url(self, bucket: str, object_key: str, expires_in: int = 900) -> str:
        """Generate a short-lived download URL for an object (default 15 minutes)."""
        pass

    @contextmanager
    @abstractmethod
    def get_local_temp_file(self, bucket: str, object_key: str) -> Iterator[str]:
        """
        Context manager providing a safe local file path for OCR and native image/PDF tools.
        Guarantees cleanup of temporary files on exit, even upon errors.
        """
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Local Storage Provider (Default for Offline Dev & Tests)
# ─────────────────────────────────────────────────────────────────────────────

class LocalStorageService(BaseStorageService):
    """
    Filesystem storage provider using local directories.
    Requires zero external credentials or network access.
    """

    def __init__(self, upload_dir: Optional[str] = None, report_dir: Optional[str] = None):
        self.upload_dir = Path(upload_dir or settings.UPLOAD_DIR).resolve()
        self.report_dir = Path(report_dir or settings.REPORT_DIR).resolve()
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_base_dir(self, bucket: str) -> Path:
        if "report" in bucket.lower():
            return self.report_dir
        return self.upload_dir

    def _resolve_local_path(self, bucket: str, object_key: str) -> Path:
        base_dir = self._resolve_base_dir(bucket)
        clean_key = object_key.strip().replace("\\", "/").lstrip("/")
        return base_dir / clean_key

    def upload_file(
        self,
        bucket: str,
        object_key: str,
        data: Union[bytes, BinaryIO],
        content_type: Optional[str] = None,
    ) -> str:
        dest_path = self._resolve_local_path(bucket, object_key)
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        content = data.read() if hasattr(data, "read") else data
        with open(dest_path, "wb") as f:
            f.write(content)

        logger.debug(f"[LocalStorage] Saved {len(content)} bytes to {dest_path}")
        return object_key

    def download_file(self, bucket: str, object_key: str) -> bytes:
        if Path(object_key).is_file():
            file_path = Path(object_key)
        else:
            file_path = self._resolve_local_path(bucket, object_key)
        if not file_path.is_file():
            # Fallback check directly in base_dir using filename only
            fallback_path = self._resolve_base_dir(bucket) / Path(object_key).name
            if fallback_path.is_file():
                file_path = fallback_path
            else:
                raise StorageFileNotFoundError(f"Local file not found for key: {object_key}")

        with open(file_path, "rb") as f:
            return f.read()

    def delete_file(self, bucket: str, object_key: str) -> bool:
        if Path(object_key).is_file():
            file_path = Path(object_key)
        else:
            file_path = self._resolve_local_path(bucket, object_key)
        deleted = False
        if file_path.is_file():
            try:
                file_path.unlink()
                deleted = True
            except OSError as e:
                logger.warning(f"[LocalStorage] Failed to unlink {file_path}: {e}")

        # Also check fallback in base_dir
        fallback_path = self._resolve_base_dir(bucket) / Path(object_key).name
        if fallback_path.is_file():
            try:
                fallback_path.unlink()
                deleted = True
            except OSError:
                pass

        return deleted

    def create_signed_url(self, bucket: str, object_key: str, expires_in: int = 900) -> str:
        # For local development, return relative static path
        clean_filename = Path(object_key).name
        endpoint = "/reports" if "report" in bucket.lower() else "/uploads"
        return f"{endpoint}/{clean_filename}"

    @contextmanager
    def get_local_temp_file(self, bucket: str, object_key: str) -> Iterator[str]:
        if Path(object_key).is_file():
            yield str(Path(object_key))
            return

        file_path = self._resolve_local_path(bucket, object_key)
        if file_path.is_file():
            yield str(file_path)
            return

        fallback_path = self._resolve_base_dir(bucket) / Path(object_key).name
        if fallback_path.is_file():
            yield str(fallback_path)
            return

        # If not on disk, write downloaded bytes to tempfile
        ext = Path(object_key).suffix
        content = self.download_file(bucket, object_key)
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            yield tmp_path
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass


# ─────────────────────────────────────────────────────────────────────────────
# Supabase Storage Provider (Private Buckets)
# ─────────────────────────────────────────────────────────────────────────────

class SupabaseStorageService(BaseStorageService):
    """
    Supabase Storage client using REST API over HTTPS.
    Enforces private buckets, service-role authorization, bounded retries,
    and opaque object paths.
    """

    def __init__(
        self,
        supabase_url: Optional[str] = None,
        service_role_key: Optional[str] = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        http_client: Optional[httpx.Client] = None,
    ):
        url_candidate = supabase_url if supabase_url is not None else getattr(settings, "SUPABASE_URL", "")
        key_candidate = service_role_key if service_role_key is not None else getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "")

        self.supabase_url = (url_candidate or "").strip().rstrip("/")
        self.service_role_key = (key_candidate or "").strip()
        self.timeout = timeout
        self.max_retries = max_retries

        if not self.supabase_url or not self.service_role_key:
            raise StorageConfigurationError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured when using Supabase storage."
            )

        self.base_url = f"{self.supabase_url}/storage/v1"
        self._custom_client = http_client

    @contextmanager
    def _client_ctx(self) -> Iterator[httpx.Client]:
        if self._custom_client is not None:
            yield self._custom_client
        else:
            with httpx.Client(timeout=self.timeout) as client:
                yield client

    def _auth_headers(self, content_type: Optional[str] = None) -> dict:
        headers = {
            "Authorization": f"Bearer {self.service_role_key}",
            "apikey": self.service_role_key,
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _execute_with_retry(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Execute HTTP request with exponential backoff on transport errors or 5xx responses."""
        backoff = 0.5
        last_error = None

        with self._client_ctx() as client:
            for attempt in range(1, self.max_retries + 1):
                try:
                    resp = client.request(method, url, **kwargs)
                    status_code = getattr(resp, "status_code", 200)
                    if isinstance(status_code, int) and status_code >= 500 and attempt < self.max_retries:
                        logger.warning(
                            f"[SupabaseStorage] Server error HTTP {status_code} (attempt {attempt}/{self.max_retries}), retrying in {backoff}s..."
                        )
                        time.sleep(backoff)
                        backoff *= 2
                        continue
                    return resp
                except (httpx.TransportError, httpx.TimeoutException) as exc:
                    last_error = exc
                    if attempt < self.max_retries:
                        logger.warning(
                            f"[SupabaseStorage] Transport error: {exc.__class__.__name__} (attempt {attempt}/{self.max_retries}), retrying in {backoff}s..."
                        )
                        time.sleep(backoff)
                        backoff *= 2
                    else:
                        break

        raise StorageError(f"Supabase storage request failed after {self.max_retries} attempts: {last_error}")

    def upload_file(
        self,
        bucket: str,
        object_key: str,
        data: Union[bytes, BinaryIO],
        content_type: Optional[str] = None,
    ) -> str:
        clean_key = object_key.strip().replace("\\", "/").lstrip("/")
        if not content_type:
            content_type, _ = mimetypes.guess_type(clean_key)
            content_type = content_type or "application/octet-stream"

        content = data.read() if hasattr(data, "read") else data
        url = f"{self.base_url}/object/{bucket}/{clean_key}"

        headers = self._auth_headers(content_type)
        headers["x-upsert"] = "true"  # Overwrite safely if same key re-uploaded

        try:
            resp = self._execute_with_retry("POST", url, content=content, headers=headers)
        except Exception as e:
            raise StorageUploadError(f"Network error during upload to '{bucket}/{clean_key}': {e}") from e

        if resp.status_code not in (200, 201):
            detail = resp.text[:200]
            raise StorageUploadError(
                f"Failed to upload object to '{bucket}/{clean_key}' (HTTP {resp.status_code}): {detail}"
            )

        logger.info(f"[SupabaseStorage] Uploaded {len(content)} bytes to '{bucket}/{clean_key}'")
        return clean_key

    def download_file(self, bucket: str, object_key: str) -> bytes:
        clean_key = object_key.strip().replace("\\", "/").lstrip("/")
        url = f"{self.base_url}/object/authenticated/{bucket}/{clean_key}"
        headers = self._auth_headers()

        resp = self._execute_with_retry("GET", url, headers=headers)

        if resp.status_code == 404:
            raise StorageFileNotFoundError(f"Object '{clean_key}' not found in bucket '{bucket}'")
        if resp.status_code != 200:
            raise StorageError(f"Failed to download object '{clean_key}' (HTTP {resp.status_code})")

        return resp.content

    def delete_file(self, bucket: str, object_key: str) -> bool:
        clean_key = object_key.strip().replace("\\", "/").lstrip("/")
        url = f"{self.base_url}/object/{bucket}"
        headers = self._auth_headers("application/json")
        payload = {"prefixes": [clean_key]}

        resp = self._execute_with_retry("DELETE", url, headers=headers, json=payload)
        if resp.status_code in (200, 204, 404):
            return True

        logger.warning(f"[SupabaseStorage] Delete returned HTTP {resp.status_code} for '{clean_key}'")
        return False

    def create_signed_url(self, bucket: str, object_key: str, expires_in: int = 900) -> str:
        clean_key = object_key.strip().replace("\\", "/").lstrip("/")
        url = f"{self.base_url}/object/sign/{bucket}/{clean_key}"
        headers = self._auth_headers("application/json")
        payload = {"expiresIn": int(expires_in)}

        resp = self._execute_with_retry("POST", url, headers=headers, json=payload)
        if resp.status_code not in (200, 201):
            raise StorageError(
                f"Failed to create signed URL for '{clean_key}' (HTTP {resp.status_code}): {resp.text[:200]}"
            )

        data = resp.json()
        signed_subpath = data.get("signedURL")
        if not signed_subpath:
            raise StorageError(f"Supabase sign response missing signedURL: {data}")

        # If signedURL is relative, prefix with base_url
        if signed_subpath.startswith("http://") or signed_subpath.startswith("https://"):
            return signed_subpath
        return f"{self.base_url}{signed_subpath}"

    @contextmanager
    def get_local_temp_file(self, bucket: str, object_key: str) -> Iterator[str]:
        raw_ext = Path(object_key).suffix
        ext = raw_ext if raw_ext in ALLOWED_EXTENSIONS else ".bin"

        content = self.download_file(bucket, object_key)
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            yield tmp_path
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError as e:
                    logger.warning(f"[SupabaseStorage] Failed to remove temp file {tmp_path}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Factory Function
# ─────────────────────────────────────────────────────────────────────────────

def get_storage_service(provider: Optional[str] = None) -> BaseStorageService:
    """
    Factory function returning the active storage service implementation.
    Defaults to LocalStorageService if STORAGE_PROVIDER is 'local' or unset.
    """
    active_provider = (provider or getattr(settings, "STORAGE_PROVIDER", "local")).strip().lower()

    if active_provider == "local":
        return LocalStorageService()
    elif active_provider == "supabase":
        return SupabaseStorageService()
    else:
        raise StorageConfigurationError(
            f"Unsupported STORAGE_PROVIDER '{active_provider}'. Must be 'local' or 'supabase'."
        )
