"""
Automated Test Suite for Phase 9: PDF Generator.
Verifies all Phase 9 architectural constraints and user corrections:
- Correction 1: Content resolution hierarchy (missing_information priority, no AI resurrection)
- Correction 2: Normalized PDF determinism (same page count, content, extracted text, ordering)
- Correction 3: Read-only Evidence Asset Resolver boundary & unavailable asset fallback
- Correction 4: Canonical POST /api/reports/{id}/generate-pdf endpoint
- Immutability of all Phase 2-8 input artifacts
- Verification gate enforcement (can_export_pdf, validation_status, blocking issues, hash mismatch)
- Evidence selection (included == True only, party isolation, no cross-particular leaks)
- Two-pass NumberedCanvas (Page X of Y, statutory watermarks)
- PDF readability and stream integrity
"""
import sys
import os
import io
import copy
import tempfile
from typing import Dict, List, Any
from unittest.mock import MagicMock, patch
from PIL import Image as PILImage
import pypdf

if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add backend directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.report_templates import get_template
from app.report_templates.schema import ReportTemplate
from app.schemas.report_context import ReportContext
from app.schemas.baseline_report import BaselineReport
from app.services.baseline_report_renderer import render_baseline_report
from app.schemas.evidence_map import EvidenceMap, EvidenceReference, EvidenceMapping
from app.services.evidence_mapper import map_evidence
from app.schemas.composed_report import ComposedReport
from app.services.report_composer import compose_report
from app.schemas.validated_report import (
    ValidationSeverity,
    ValidationIssue,
    ValidatedReport,
)
from app.services.report_validator import validate_report
from app.schemas.report_review import (
    ReportReviewState,
    ParticularReview,
    EvidenceSelection,
)
from app.services.report_review_editor import (
    create_review_state,
    apply_particular_edit,
    apply_evidence_selection,
    revalidate_review_state,
)
from app.schemas.pdf_report import (
    PdfGenerationRequest,
    PdfGenerationResult,
    PdfExportBlockedError,
)
from app.services.pdf_generator import (
    generate_pdf_report,
    validate_export_gate,
    resolve_particular_text,
    EvidenceAssetResolver,
)

from test_phase7_report_validator import create_mock_report_context, create_mock_composed_report


def build_pipeline_fixtures():
    """Generates all Phase 2-8 fixtures for PDF generator testing."""
    context = create_mock_report_context()
    template = get_template(context.metadata.template_key, context.metadata.template_version)
    baseline = render_baseline_report(context, template.template_key, template.template_version)
    evidence_map = map_evidence(context, template)
    composed = create_mock_composed_report(context, template, evidence_map)
    validated = validate_report(context=context, template=template, evidence_map=evidence_map, composed_report=composed)
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    return context, template, baseline, evidence_map, composed, validated, state


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 9 TESTS
# ─────────────────────────────────────────────────────────────────────────────

def test_01_valid_report_generates_pdf():
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
        generation_timestamp="2026-09-21T12:00:00Z",
        generated_by="Officer Test User",
    )
    result = generate_pdf_report(req)
    assert result is not None
    assert result.pdf_bytes.startswith(b"%PDF-")
    assert result.page_count >= 1
    assert result.file_size > 5000
    assert result.input_hash == state.input_hash
    print("PASS: test_01_valid_report_generates_pdf")


def test_02_invalid_report_rejected():
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    state.validation_status = "INVALID"
    state.can_export_pdf = False
    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
    )
    try:
        generate_pdf_report(req)
        assert False, "Should have raised PdfExportBlockedError"
    except PdfExportBlockedError as e:
        assert len(e.gate_reasons) >= 1
        print("PASS: test_02_invalid_report_rejected")


def test_03_blocking_issue_rejected():
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    state.blocking_issues.append(
        ValidationIssue(
            code="UNRESOLVED_DISCREPANCY",
            severity=ValidationSeverity.BLOCKING,
            message="Unresolved discrepancy",
        )
    )
    state.can_export_pdf = False
    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
    )
    try:
        generate_pdf_report(req)
        assert False, "Should have raised PdfExportBlockedError"
    except PdfExportBlockedError as e:
        assert any("BLOCKING" in r for r in e.gate_reasons)
        print("PASS: test_03_blocking_issue_rejected")


def test_04_can_export_pdf_false_rejected():
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    state.can_export_pdf = False
    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
    )
    try:
        generate_pdf_report(req)
        assert False, "Should have raised PdfExportBlockedError"
    except PdfExportBlockedError as e:
        assert any("can_export_pdf" in r for r in e.gate_reasons)
        print("PASS: test_04_can_export_pdf_false_rejected")


def test_05_input_hash_mismatch_rejected():
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    evidence_map.input_hash = "mismatched_sha256_hash_123456"
    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
    )
    try:
        generate_pdf_report(req)
        assert False, "Should have raised PdfExportBlockedError"
    except PdfExportBlockedError as e:
        assert any("Hash mismatch" in r for r in e.gate_reasons)
        print("PASS: test_05_input_hash_mismatch_rejected")


def test_06_template_mismatch_rejected():
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    state.template_version = "v2.0"
    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
    )
    try:
        generate_pdf_report(req)
        assert False, "Should have raised PdfExportBlockedError"
    except PdfExportBlockedError as e:
        assert any("Template version mismatch" in r for r in e.gate_reasons)
        print("PASS: test_06_template_mismatch_rejected")


def test_07_missing_particular_rejected():
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    del state.particular_reviews["P1"]
    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
    )
    try:
        generate_pdf_report(req)
        assert False, "Should have raised PdfExportBlockedError"
    except PdfExportBlockedError as e:
        assert any("P1" in r for r in e.gate_reasons)
        print("PASS: test_07_missing_particular_rejected")


def test_08_current_edited_text_appears_in_pdf():
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    edited_marker = "OFFICER_CONFIRMED_RESIDENCE_OBSERVATION_FLAG_XYZ999"
    p2 = state.particular_reviews["P2"]
    p2.current_text = edited_marker

    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
        generation_timestamp="2026-09-21T12:00:00Z",
    )
    result = generate_pdf_report(req)
    reader = pypdf.PdfReader(io.BytesIO(result.pdf_bytes))
    full_text = "".join(page.extract_text() for page in reader.pages)
    assert edited_marker in full_text
    print("PASS: test_08_current_edited_text_appears_in_pdf")


def test_09_original_ai_text_not_substituted_after_edit():
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    ai_secret = "AI_FABRICATED_NARRATIVE_DO_NOT_PRINT_ABC123"
    edited_text = "AUTHENTIC_OFFICER_CORRECTED_TEXT_DEF456"

    p1 = state.particular_reviews["P1"]
    p1.original_ai_text = ai_secret
    p1.current_text = edited_text

    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
        generation_timestamp="2026-09-21T12:00:00Z",
    )
    result = generate_pdf_report(req)
    reader = pypdf.PdfReader(io.BytesIO(result.pdf_bytes))
    full_text = "".join(page.extract_text() for page in reader.pages)
    assert edited_text in full_text
    assert ai_secret not in full_text
    print("PASS: test_09_original_ai_text_not_substituted_after_edit")


def test_10_excluded_evidence_does_not_appear():
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    # Find an evidence item to exclude
    # Target finding:304 which is mapped strictly and solely to P6
    p6_evidence = evidence_map.get_evidence_for_particular("P6")
    target_refs = [e for e in p6_evidence if e.evidence_id == "finding:304"]
    assert len(target_refs) > 0
    target_ref = target_refs[0]

    # Set selection preference to excluded in P6
    state.evidence_selections["P6"] = [
        EvidenceSelection(particular_id="P6", evidence_id=target_ref.evidence_id, included=False)
    ]

    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
        generation_timestamp="2026-09-21T12:00:00Z",
    )
    result = generate_pdf_report(req)
    reader = pypdf.PdfReader(io.BytesIO(result.pdf_bytes))
    full_text = "".join(page.extract_text() for page in reader.pages)
    
    # Target evidence ID should NOT be in the rendered text
    assert f"[ID: {target_ref.evidence_id}]" not in full_text
    print("PASS: test_10_excluded_evidence_does_not_appear")


def test_11_included_evidence_appears():
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    # Target finding:304 in P6
    p6_evidence = evidence_map.get_evidence_for_particular("P6")
    target_refs = [e for e in p6_evidence if e.evidence_id == "finding:304"]
    assert len(target_refs) > 0
    target_ref = target_refs[0]

    state.evidence_selections["P6"] = [
        EvidenceSelection(particular_id="P6", evidence_id=target_ref.evidence_id, included=True)
    ]

    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
        generation_timestamp="2026-09-21T12:00:00Z",
    )
    result = generate_pdf_report(req)
    reader = pypdf.PdfReader(io.BytesIO(result.pdf_bytes))
    full_text = "".join(page.extract_text() for page in reader.pages)
    
    # Target evidence ID must be present
    assert f"[ID: {target_ref.evidence_id}]" in full_text
    print("PASS: test_11_included_evidence_appears")


def test_12_evidence_cannot_cross_particulars():
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    # finding:304 is mapped strictly to P6
    p6_evidence = evidence_map.get_evidence_for_particular("P6")
    target_refs = [e for e in p6_evidence if e.evidence_id == "finding:304"]
    assert len(target_refs) > 0
    target_ref = target_refs[0]

    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
        generation_timestamp="2026-09-21T12:00:00Z",
    )
    result = generate_pdf_report(req)
    reader = pypdf.PdfReader(io.BytesIO(result.pdf_bytes))
    
    # Extract text from annexure pages
    evidence_pages_text = ""
    for page in reader.pages:
        txt = page.extract_text()
        if "ANNEXURE: MAPPED VERIFICATION EVIDENCE DOSSIER" in txt or "Particular [" in txt:
            evidence_pages_text += txt + "\n"

    # Verify target_ref appears strictly inside Particular [P6] block
    assert f"[ID: {target_ref.evidence_id}]" in evidence_pages_text
    p6_pos = evidence_pages_text.find("Particular [P6]")
    ref_pos = evidence_pages_text.find(f"[ID: {target_ref.evidence_id}]")
    p7_pos = evidence_pages_text.find("Particular [P7")
    
    assert p6_pos != -1, "Particular [P6] header not found"
    assert ref_pos > p6_pos, "Evidence reference should be after Particular [P6] header"
    if p7_pos != -1:
        assert ref_pos < p7_pos, "Evidence reference should not cross into Particular [P7]"
    print("PASS: test_12_evidence_cannot_cross_particulars")


def test_13_applicant_guarantor_isolation():
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    # Check that party badges render accurately
    p1_evidence = evidence_map.get_evidence_for_particular("P1")
    assert len(p1_evidence) > 0
    
    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
        generation_timestamp="2026-09-21T12:00:00Z",
    )
    result = generate_pdf_report(req)
    reader = pypdf.PdfReader(io.BytesIO(result.pdf_bytes))
    full_text = "".join(page.extract_text() for page in reader.pages)
    
    assert "[APPLICANT]" in full_text
    print("PASS: test_13_applicant_guarantor_isolation")


def test_14_missing_information_priority_over_baseline():
    """
    Correction 1 Verification:
    IF missing_information exists AND current_text is empty:
        render INFORMATION REQUIRED (NOT baseline text)
    """
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    
    p2 = state.particular_reviews["P2"]
    p2.missing_information = ["Physical visit geo-tagged photograph absent"]
    p2.current_text = "" # empty current_text

    resolved = resolve_particular_text("P2", p2, baseline)
    assert resolved.startswith("[INFORMATION REQUIRED:")
    assert "Physical visit geo-tagged photograph absent" in resolved

    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
        generation_timestamp="2026-09-21T12:00:00Z",
    )
    result = generate_pdf_report(req)
    reader = pypdf.PdfReader(io.BytesIO(result.pdf_bytes))
    full_text = "".join(page.extract_text() for page in reader.pages)
    assert "INFORMATION REQUIRED" in full_text
    print("PASS: test_14_missing_information_priority_over_baseline")


def test_15_long_verification_details_wraps_correctly():
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    long_narrative = (
        "During the course of re-verification, the investigating official performed in-depth "
        "cross-verification across multiple public and private data repositories. "
    ) * 15 # ~300 words
    p8 = state.particular_reviews["P8"]
    p8.current_text = long_narrative

    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
        generation_timestamp="2026-09-21T12:00:00Z",
    )
    result = generate_pdf_report(req)
    assert result.page_count >= 1
    reader = pypdf.PdfReader(io.BytesIO(result.pdf_bytes))
    full_text = "".join(page.extract_text() for page in reader.pages)
    assert "investigating official performed in-depth" in full_text
    print("PASS: test_15_long_verification_details_wraps_correctly")


def test_16_tables_and_page_breaks_remain_valid():
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
        generation_timestamp="2026-09-21T12:00:00Z",
    )
    result = generate_pdf_report(req)
    reader = pypdf.PdfReader(io.BytesIO(result.pdf_bytes))
    # Standard 27-particulars + footer + annexure spans at least 2 pages
    assert len(reader.pages) >= 2
    # Verify repeat header or running headers exist across pages
    page2_text = reader.pages[1].extract_text()
    assert "SMARTVERIFY" in page2_text or "Particulars" in page2_text or "ANNEXURE" in page2_text
    print("PASS: test_16_tables_and_page_breaks_remain_valid")


def test_17_image_evidence_renders_correctly():
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    
    # Create temporary mock image file
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_img:
        tmp_path = tmp_img.name
        img = PILImage.new("RGB", (400, 300), color=(30, 58, 138))
        img.save(tmp_path, "PNG")

    try:
        # Add evidence reference with image file
        img_ref = EvidenceReference(
            evidence_id="doc:test_site_img",
            evidence_type="document",
            source="documents",
            source_id="999",
            filename=tmp_path,
            document_type="Physical Site Photograph",
            party_id="applicant",
            party_name="Applicant Ramesh Kumar",
            particular_id="P2A",
            evidence_category="residence_evidence",
            status="VERIFIED",
            metadata={"file_size": 1234},
        )
        evidence_map.particular_mappings["P2A"].evidence.append(img_ref)
        
        # Test EvidenceAssetResolver directly
        asset = EvidenceAssetResolver.resolve_asset(img_ref)
        assert asset.is_image is True
        assert asset.image_path == tmp_path
        assert asset.image_dims[0] <= EvidenceAssetResolver.MAX_IMAGE_WIDTH
        assert asset.image_dims[1] <= EvidenceAssetResolver.MAX_IMAGE_HEIGHT

        # Test generation with the image
        req = PdfGenerationRequest(
            review_state=state,
            template=template,
            baseline_report=baseline,
            evidence_map=evidence_map,
            validated_report=validated,
            generation_timestamp="2026-09-21T12:00:00Z",
        )
        result = generate_pdf_report(req)
        assert result.file_size > 5000
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    print("PASS: test_17_image_evidence_renders_correctly")


def test_18_multiple_evidence_pages_render_correctly():
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    # Add multiple evidence entries across particulars
    for i in range(12):
        ref = EvidenceReference(
            evidence_id=f"finding:extra_{i}",
            evidence_type="finding",
            source="application_findings",
            source_id=f"f_{i}",
            particular_id="P4B",
            evidence_category="income_documents",
            status="VERIFIED",
            metadata={"finding_item": f"Turnover check batch {i}"},
        )
        evidence_map.particular_mappings["P4B"].evidence.append(ref)

    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
        generation_timestamp="2026-09-21T12:00:00Z",
    )
    result = generate_pdf_report(req)
    assert result.page_count >= 3
    print("PASS: test_18_multiple_evidence_pages_render_correctly")


def test_19_page_numbers_generated():
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
        generation_timestamp="2026-09-21T12:00:00Z",
    )
    result = generate_pdf_report(req)
    reader = pypdf.PdfReader(io.BytesIO(result.pdf_bytes))
    page1_text = reader.pages[0].extract_text()
    assert f"Page 1 of {result.page_count}" in page1_text
    print("PASS: test_19_page_numbers_generated")


def test_20_final_pdf_is_readable_openable():
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
        generation_timestamp="2026-09-21T12:00:00Z",
    )
    result = generate_pdf_report(req)
    reader = pypdf.PdfReader(io.BytesIO(result.pdf_bytes))
    assert len(reader.pages) == result.page_count
    for i, p in enumerate(reader.pages):
        t = p.extract_text()
        assert len(t) > 20
    print("PASS: test_20_final_pdf_is_readable_openable")


def test_21_input_artifacts_remain_unchanged():
    """Verify strict immutability of all input objects."""
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    
    state_before = state.model_dump()
    evidence_before = evidence_map.model_dump()
    baseline_before = baseline.model_dump()
    validated_before = validated.model_dump()

    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
        generation_timestamp="2026-09-21T12:00:00Z",
    )
    generate_pdf_report(req)

    assert state.model_dump() == state_before
    assert evidence_map.model_dump() == evidence_before
    assert baseline.model_dump() == baseline_before
    assert validated.model_dump() == validated_before
    print("PASS: test_21_input_artifacts_remain_unchanged")


def test_22_normalized_deterministic_repeat_generation():
    """
    Correction 2 Verification:
    Same inputs + pinned timestamp -> same page count, content, extracted text, ordering.
    """
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    req1 = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
        generation_timestamp="2026-09-21T12:00:00Z",
        generated_by="Deterministic Officer",
    )
    result1 = generate_pdf_report(req1)

    req2 = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
        generation_timestamp="2026-09-21T12:00:00Z",
        generated_by="Deterministic Officer",
    )
    result2 = generate_pdf_report(req2)

    # 1. Page count identity
    assert result1.page_count == result2.page_count

    # 2. Text extraction identity across all pages
    reader1 = pypdf.PdfReader(io.BytesIO(result1.pdf_bytes))
    reader2 = pypdf.PdfReader(io.BytesIO(result2.pdf_bytes))
    assert len(reader1.pages) == len(reader2.pages)

    for idx in range(len(reader1.pages)):
        text1 = reader1.pages[idx].extract_text()
        text2 = reader2.pages[idx].extract_text()
        assert text1 == text2

    print("PASS: test_22_normalized_deterministic_repeat_generation")


def test_23_evidence_asset_resolver_isolation_and_unavailable_asset():
    """
    Correction 3 Verification:
    Evidence asset resolver handles missing or unreadable files cleanly
    by returning a deterministic neutral notice without throwing or crashing the report.
    """
    missing_ref = EvidenceReference(
        evidence_id="doc:non_existent_file",
        evidence_type="document",
        source="documents",
        source_id="404",
        filename="/tmp/safe/path/that/does/not/exist/image_9999.png",
        document_type="Missing Property Document",
        party_id="applicant",
        party_name="Applicant Ramesh",
        particular_id="P7B1",
        evidence_category="rc_evidence",
        status="PENDING",
    )
    asset = EvidenceAssetResolver.resolve_asset(missing_ref)
    assert asset.is_image is False
    assert asset.status_note == "Evidence asset unavailable for rendering."

    # Verify rendering report with unavailable asset does NOT crash
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    evidence_map.particular_mappings["P7B1"].evidence.append(missing_ref)

    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
        generation_timestamp="2026-09-21T12:00:00Z",
    )
    result = generate_pdf_report(req)
    reader = pypdf.PdfReader(io.BytesIO(result.pdf_bytes))
    full_text = "".join(page.extract_text() for page in reader.pages)
    assert "Evidence asset unavailable for rendering." in full_text
    print("PASS: test_23_evidence_asset_resolver_isolation_and_unavailable_asset")


def test_24_canonical_api_endpoint():
    """
    Correction 4 Verification:
    POST /api/reports/{id}/generate-pdf endpoint behavior.
    """
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api.endpoints import reports
    from app.models.user import User

    client = TestClient(app)

    # Setup in-memory review session directly in reports endpoint registry
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    app_id = 9999
    state.application_id = app_id
    reports._review_sessions[app_id] = {
        "state": state,
        "context": context,
        "evidence_map": evidence_map,
        "template": template,
        "baseline": baseline,
        "validated": validated,
    }

    # Override get_current_user dependency
    mock_user = User(id=1, email="officer@bank.com", role="officer")
    mock_user.name = "Officer Test"
    app.dependency_overrides[reports.get_current_user] = lambda: mock_user

    try:
        # 1. Successful generation
        res = client.post(f"/api/reports/{app_id}/generate-pdf")
        assert res.status_code == 200
        assert res.headers["content-type"] == "application/pdf"
        assert res.content.startswith(b"%PDF-")
        assert f"SmartVerify_Report_{app_id}" in res.headers.get("content-disposition", "")

        # 2. Blocked generation when can_export_pdf is False
        state.can_export_pdf = False
        res_blocked = client.post(f"/api/reports/{app_id}/generate-pdf")
        assert res_blocked.status_code == 422

        # 3. Non-existent session
        res_404 = client.post("/api/reports/888888/generate-pdf")
        assert res_404.status_code == 404
    finally:
        app.dependency_overrides.clear()
        if app_id in reports._review_sessions:
            del reports._review_sessions[app_id]
    print("PASS: test_24_canonical_api_endpoint")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("RUNNING PHASE 9 PDF GENERATOR TESTS (24 TESTS)")
    print("=" * 60)

    tests = [
        test_01_valid_report_generates_pdf,
        test_02_invalid_report_rejected,
        test_03_blocking_issue_rejected,
        test_04_can_export_pdf_false_rejected,
        test_05_input_hash_mismatch_rejected,
        test_06_template_mismatch_rejected,
        test_07_missing_particular_rejected,
        test_08_current_edited_text_appears_in_pdf,
        test_09_original_ai_text_not_substituted_after_edit,
        test_10_excluded_evidence_does_not_appear,
        test_11_included_evidence_appears,
        test_12_evidence_cannot_cross_particulars,
        test_13_applicant_guarantor_isolation,
        test_14_missing_information_priority_over_baseline,
        test_15_long_verification_details_wraps_correctly,
        test_16_tables_and_page_breaks_remain_valid,
        test_17_image_evidence_renders_correctly,
        test_18_multiple_evidence_pages_render_correctly,
        test_19_page_numbers_generated,
        test_20_final_pdf_is_readable_openable,
        test_21_input_artifacts_remain_unchanged,
        test_22_normalized_deterministic_repeat_generation,
        test_23_evidence_asset_resolver_isolation_and_unavailable_asset,
        test_24_canonical_api_endpoint,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"FAIL: {t.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 60)
    print(f"PHASE 9 TEST RESULTS: {passed} PASSED, {failed} FAILED (TOTAL {len(tests)})")
    print("=" * 60)
    if failed > 0:
        sys.exit(1)
