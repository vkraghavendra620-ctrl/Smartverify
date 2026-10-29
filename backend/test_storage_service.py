"""
Offline unit tests for SmartVerify Storage Service.
Tests LocalStorageService, SupabaseStorageService (via mocked HTTP),
key generation without PII, temporary file cleanup, and error handling.
Does NOT make live network or LLM calls.
"""

import os
from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch
import pytest
import httpx

from app.services.storage_service import (
    generate_object_key,
    get_storage_service,
    LocalStorageService,
    SupabaseStorageService,
    StorageError,
    StorageConfigurationError,
    StorageFileNotFoundError,
    StorageUploadError,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Opaque Key Generation Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_generate_object_key_format_and_pii_isolation():
    key = generate_object_key(
        category="documents",
        application_id=101,
        original_filename="Rahul_Sharma_Aadhaar_123456789012.pdf",
    )

    # Key format must match applications/{id}/{category}/{uuid}{ext}
    assert key.startswith("applications/101/documents/")
    assert key.endswith(".pdf")

    # Critical PII check: key must NOT leak applicant name, Aadhaar, or PAN
    assert "Rahul" not in key
    assert "Sharma" not in key
    assert "123456789012" not in key

    # Distinct calls produce unique keys
    key2 = generate_object_key("documents", 101, "another.pdf")
    assert key != key2


def test_generate_object_key_category_and_extension_sanitization():
    # Reports default to .pdf
    report_key = generate_object_key("reports", 42, "final_report")
    assert report_key.startswith("applications/42/reports/")
    assert report_key.endswith(".pdf")

    # Invalid extension falls back to .bin for documents
    unknown_key = generate_object_key("documents", 42, "malicious.exe")
    assert unknown_key.endswith(".bin")

    # Unrecognized category falls back to documents
    custom_cat_key = generate_object_key("invalid_cat", 42, "image.png")
    assert "applications/42/documents/" in custom_cat_key
    assert custom_cat_key.endswith(".png")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Local Storage Provider Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_local_storage_lifecycle(tmp_path):
    upload_dir = tmp_path / "uploads"
    report_dir = tmp_path / "reports"
    service = LocalStorageService(upload_dir=str(upload_dir), report_dir=str(report_dir))

    bucket = "applicant-documents"
    object_key = "applications/99/documents/test_doc.txt"
    test_data = b"SmartVerify offline document content"

    # 1. Upload
    res_key = service.upload_file(bucket, object_key, test_data)
    assert res_key == object_key
    assert (upload_dir / object_key).is_file()

    # 2. Download
    downloaded = service.download_file(bucket, object_key)
    assert downloaded == test_data

    # 3. Signed URL (local relative static URL)
    url = service.create_signed_url(bucket, object_key)
    assert url == "/uploads/test_doc.txt"

    # 4. Temp file context manager
    with service.get_local_temp_file(bucket, object_key) as temp_path:
        assert os.path.exists(temp_path)
        with open(temp_path, "rb") as f:
            assert f.read() == test_data

    # 5. Delete
    deleted = service.delete_file(bucket, object_key)
    assert deleted is True
    assert not (upload_dir / object_key).exists()

    # Repeated delete returns False
    assert service.delete_file(bucket, object_key) is False


def test_local_storage_file_not_found(tmp_path):
    service = LocalStorageService(upload_dir=str(tmp_path / "u"), report_dir=str(tmp_path / "r"))
    with pytest.raises(StorageFileNotFoundError):
        service.download_file("applicant-documents", "non_existent_key.pdf")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Supabase Storage Provider Tests (Mocked Offline)
# ─────────────────────────────────────────────────────────────────────────────

def test_supabase_storage_missing_configuration():
    # Both URL and key are required
    with pytest.raises(StorageConfigurationError):
        SupabaseStorageService(supabase_url="", service_role_key="")

    with pytest.raises(StorageConfigurationError):
        SupabaseStorageService(supabase_url="https://test.supabase.co", service_role_key="")


def test_supabase_storage_upload_success():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.request.return_value = httpx.Response(200, text="{}")

    service = SupabaseStorageService(
        supabase_url="https://project.supabase.co",
        service_role_key="secret-service-role-key",
        http_client=mock_client,
    )

    data = b"Pan Card Scan Bytes"
    res = service.upload_file("applicant-documents", "applications/1/documents/pan.jpg", data, "image/jpeg")

    assert res == "applications/1/documents/pan.jpg"
    assert mock_client.request.called
    args, kwargs = mock_client.request.call_args
    assert args[0] == "POST"
    assert "https://project.supabase.co/storage/v1/object/applicant-documents/applications/1/documents/pan.jpg" in args[1]
    assert kwargs["headers"]["Authorization"] == "Bearer secret-service-role-key"
    assert kwargs["headers"]["apikey"] == "secret-service-role-key"
    assert kwargs["headers"]["Content-Type"] == "image/jpeg"
    assert kwargs["headers"]["x-upsert"] == "true"
    assert kwargs["content"] == data


def test_supabase_storage_upload_failure_raises_upload_error():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.request.return_value = httpx.Response(403, text="Unauthorized bucket access")

    service = SupabaseStorageService(
        supabase_url="https://project.supabase.co",
        service_role_key="secret-key",
        http_client=mock_client,
    )

    with pytest.raises(StorageUploadError) as exc_info:
        service.upload_file("applicant-documents", "test.pdf", b"data")
    assert "Failed to upload" in str(exc_info.value)
    assert "403" in str(exc_info.value)


def test_supabase_storage_download_success_and_404():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.request.return_value = httpx.Response(200, content=b"PDF_REPORT_BYTES")

    service = SupabaseStorageService(
        supabase_url="https://project.supabase.co",
        service_role_key="secret-key",
        http_client=mock_client,
    )

    content = service.download_file("generated-reports", "rep.pdf")
    assert content == b"PDF_REPORT_BYTES"

    # Test 404 behavior
    mock_client.request.return_value = httpx.Response(404, text="Not Found")

    with pytest.raises(StorageFileNotFoundError):
        service.download_file("generated-reports", "missing.pdf")


def test_supabase_storage_delete_success():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.request.return_value = httpx.Response(200, text="{}")

    service = SupabaseStorageService(
        supabase_url="https://project.supabase.co",
        service_role_key="secret-key",
        http_client=mock_client,
    )

    assert service.delete_file("applicant-documents", "doc.pdf") is True
    args, kwargs = mock_client.request.call_args
    assert args[0] == "DELETE"
    assert kwargs["json"] == {"prefixes": ["doc.pdf"]}


def test_supabase_storage_create_signed_url():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.request.return_value = httpx.Response(
        200, json={"signedURL": "/object/sign/applicant-documents/doc.pdf?token=exp123"}
    )

    service = SupabaseStorageService(
        supabase_url="https://project.supabase.co",
        service_role_key="secret-key",
        http_client=mock_client,
    )

    signed_url = service.create_signed_url("applicant-documents", "doc.pdf", expires_in=600)
    assert signed_url == "https://project.supabase.co/storage/v1/object/sign/applicant-documents/doc.pdf?token=exp123"
    args, kwargs = mock_client.request.call_args
    assert kwargs["json"] == {"expiresIn": 600}


def test_supabase_storage_temp_file_cleanup_on_success_and_exception():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.request.return_value = httpx.Response(200, content=b"SIMULATED_IMAGE_FOR_OCR")

    service = SupabaseStorageService(
        supabase_url="https://project.supabase.co",
        service_role_key="secret-key",
        http_client=mock_client,
    )

    captured_temp_path = None

    # Case 1: Normal block exit cleans up temp file
    with service.get_local_temp_file("applicant-documents", "aadhaar.png") as temp_file:
        captured_temp_path = temp_file
        assert os.path.exists(temp_file)
        assert temp_file.endswith(".png")
        with open(temp_file, "rb") as f:
            assert f.read() == b"SIMULATED_IMAGE_FOR_OCR"

    assert not os.path.exists(captured_temp_path), "Temp file should be deleted on context exit"

    # Case 2: Exception inside block still cleans up temp file
    captured_err_path = None
    try:
        with service.get_local_temp_file("applicant-documents", "aadhaar.jpg") as temp_file2:
            captured_err_path = temp_file2
            assert os.path.exists(temp_file2)
            raise RuntimeError("OCR processing failed abruptly")
    except RuntimeError:
        pass

    assert not os.path.exists(captured_err_path), "Temp file should be deleted even on exception"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Storage Factory Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_get_storage_service_factory():
    # Default is local
    s1 = get_storage_service("local")
    assert isinstance(s1, LocalStorageService)

    # Supabase provider with configured credentials
    with patch("app.services.storage_service.settings") as mock_settings:
        mock_settings.SUPABASE_URL = "https://example.supabase.co"
        mock_settings.SUPABASE_SERVICE_ROLE_KEY = "test-key"
        s2 = get_storage_service("supabase")
        assert isinstance(s2, SupabaseStorageService)

    # Unsupported provider raises StorageConfigurationError
    with pytest.raises(StorageConfigurationError):
        get_storage_service("s3_unsupported")
