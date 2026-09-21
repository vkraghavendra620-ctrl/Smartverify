"""Report retrieval endpoint."""
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
import os

from app.db.database import get_db
from app.models.verification_report import VerificationReport
from app.models.user import User
from app.schemas.report import ReportOut
from app.core.security import get_current_user
from app.services.report_service import generate_report

router = APIRouter()


@router.get("/{application_id}", response_model=ReportOut)
def get_report(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = db.query(VerificationReport).filter(
        VerificationReport.application_id == application_id
    ).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.get("/{application_id}/download")
def download_report(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = db.query(VerificationReport).filter(
        VerificationReport.application_id == application_id
    ).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    # Always regenerate to ensure the current report engine (V6) is used.
    try:
        pdf_path = generate_report(application_id=application_id, db=db)
        report.pdf_path = pdf_path
        db.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {e}")

    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=f"SmartVerify_Report_{application_id}.pdf",
    )


@router.post("/{application_id}/regenerate-pdf")
def regenerate_pdf(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """On-demand PDF regeneration without re-running the AI pipeline."""
    report = db.query(VerificationReport).filter(
        VerificationReport.application_id == application_id
    ).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found. Run AI verification first.")

    try:
        pdf_path = generate_report(application_id=application_id, db=db)
        report.pdf_path = pdf_path
        db.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {e}")

    return {"message": "PDF regenerated successfully", "pdf_path": pdf_path}


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 8: REPORT PREVIEW / REVIEW EDITOR ENDPOINTS (IN-MEMORY ONLY)
# ─────────────────────────────────────────────────────────────────────────────
from typing import Dict, Any
from app.report_templates import get_template
from app.schemas.report_review import (
    ReportReviewState,
    ParticularEditRequest,
    EvidenceToggleRequest,
)
from app.services.report_context_builder import build_report_context
from app.services.baseline_report_renderer import render_baseline_report
from app.services.evidence_mapper import map_evidence
from app.services.report_composer import compose_report
from app.services.report_validator import validate_report
from app.services.report_review_editor import (
    create_review_state,
    apply_particular_edit,
    apply_evidence_selection,
    revalidate_review_state,
)

from app.schemas.pdf_report import PdfGenerationRequest, PdfExportBlockedError
from app.services.pdf_generator import generate_pdf_report

# In-memory review session registry (ZERO database writes)
_review_sessions: Dict[int, Dict[str, Any]] = {}


@router.get("/{application_id}/review", response_model=ReportReviewState)
def get_report_review(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retrieve or initialize an in-memory review session for the given application.
    Does NOT write to database, does NOT call Gemini, does NOT generate PDF.
    """
    if application_id in _review_sessions:
        return _review_sessions[application_id]["state"]

    template = get_template("standard_reverification", "v1.0")
    try:
        context = build_report_context(application_id, db, template.template_key, template.template_version)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Failed to build report context: {e}")

    baseline = render_baseline_report(context, template.template_key, template.template_version)
    evidence_map = map_evidence(context, template)
    composed = compose_report(context, template=template, evidence_map=evidence_map)
    validated = validate_report(context=context, template=template, evidence_map=evidence_map, composed_report=composed)

    state = create_review_state(baseline, evidence_map, composed, validated, template)
    _review_sessions[application_id] = {
        "state": state,
        "context": context,
        "evidence_map": evidence_map,
        "template": template,
        "baseline": baseline,
        "validated": validated,
    }
    return state


@router.post("/{application_id}/review/edit", response_model=ReportReviewState)
def edit_particular_review(
    application_id: int,
    edit_req: ParticularEditRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Apply manual Verification Details text edit.
    Records audit entry, increments revision, executes Phase 7 revalidation.
    """
    session = _review_sessions.get(application_id)
    if not session:
        raise HTTPException(status_code=404, detail="Review session not initialized. Fetch review first.")

    try:
        updated_state = apply_particular_edit(
            state=session["state"],
            context=session["context"],
            evidence_map=session["evidence_map"],
            template=session["template"],
            edit_req=edit_req,
        )
        session["state"] = updated_state
        return updated_state
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{application_id}/review/evidence", response_model=ReportReviewState)
def toggle_evidence_selection(
    application_id: int,
    toggle_req: EvidenceToggleRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Toggle inclusion/exclusion for a mapped evidence item.
    Guards applicant/guarantor isolation and does NOT mutate Phase 5 evidence map.
    """
    session = _review_sessions.get(application_id)
    if not session:
        raise HTTPException(status_code=404, detail="Review session not initialized.")

    try:
        updated_state = apply_evidence_selection(
            state=session["state"],
            particular_id=toggle_req.particular_id,
            evidence_id=toggle_req.evidence_id,
            included=toggle_req.included,
            display_order=toggle_req.display_order,
        )
        session["state"] = updated_state
        return updated_state
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{application_id}/review/revalidate", response_model=ReportReviewState)
def revalidate_review(
    application_id: int,
    current_user: User = Depends(get_current_user),
):
    """
    Deterministically revalidate the current review state using Phase 7 validator.
    """
    session = _review_sessions.get(application_id)
    if not session:
        raise HTTPException(status_code=404, detail="Review session not initialized.")

    updated_state = revalidate_review_state(
        state=session["state"],
        context=session["context"],
        evidence_map=session["evidence_map"],
        template=session["template"],
    )
    session["state"] = updated_state
    return updated_state


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 9: CANONICAL PDF GENERATION ENDPOINT (READ-ONLY IN-MEMORY)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{application_id}/generate-pdf")
def generate_approved_pdf(
    application_id: int,
    current_user: User = Depends(get_current_user),
):
    """
    Canonical Phase 9 PDF generation endpoint.
    - Consumes active approved in-memory review state
    - Enforces Phase 8 can_export_pdf gate
    - Enforces Phase 9 validate_export_gate
    - Renders publication-quality PDF
    - Returns application/pdf streaming response
    - ZERO database mutation, ZERO PDF persistence
    """
    session = _review_sessions.get(application_id)
    if not session:
        raise HTTPException(status_code=404, detail="Review session not initialized. Fetch review first.")

    state: ReportReviewState = session["state"]
    if not state.can_export_pdf:
        raise HTTPException(
            status_code=422,
            detail="PDF generation blocked: Review state has can_export_pdf=False or contains unresolved blocking issues."
        )

    try:
        req = PdfGenerationRequest(
            review_state=state,
            template=session["template"],
            baseline_report=session.get("baseline"),
            evidence_map=session["evidence_map"],
            validated_report=session.get("validated"),
            generated_by=getattr(current_user, "name", "Verification Officer") or "Verification Officer",
        )
        result = generate_pdf_report(req)

        return Response(
            content=result.pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="SmartVerify_Report_{application_id}_rev{state.revision}.pdf"',
                "X-Report-Pages": str(result.page_count),
                "X-Report-Hash": result.input_hash,
                "X-Template-Version": result.template_version,
            },
        )
    except PdfExportBlockedError as e:
        raise HTTPException(
            status_code=422,
            detail={"error": e.message, "gate_reasons": e.gate_reasons}
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"PDF generation failed: {str(e)}"
        )

