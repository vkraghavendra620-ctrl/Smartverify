"""
PDF Report Generation Schemas for SmartVerify Report Generation System (Phase 9).
Defines strongly-typed input models, generation results, and export gate exceptions.
"""
from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field

from app.report_templates.schema import ReportTemplate
from app.schemas.baseline_report import BaselineReport
from app.schemas.evidence_map import EvidenceMap
from app.schemas.validated_report import ValidatedReport
from app.schemas.report_review import ReportReviewState


class PdfGenerationRequest(BaseModel):
    """
    Immutable container holding all read-only inputs required to render a final PDF report.
    Zero direct database queries, zero LLM calls, and zero file mutations.
    """
    review_state: ReportReviewState = Field(
        ...,
        description="Phase 8 approved in-memory review state with officer edits and selections"
    )
    template: ReportTemplate = Field(
        ...,
        description="Phase 2 fixed report template specification"
    )
    baseline_report: Optional[BaselineReport] = Field(
        None,
        description="Phase 4 deterministic baseline report for deterministic fallback"
    )
    evidence_map: EvidenceMap = Field(
        ...,
        description="Phase 5 immutable evidence map containing mapped evidence references"
    )
    validated_report: ValidatedReport = Field(
        ...,
        description="Phase 7 validated report package"
    )
    generation_timestamp: Optional[str] = Field(
        None,
        description="Optional pinned ISO-8601 UTC timestamp for deterministic test reproducibility"
    )
    generated_by: str = Field(
        "SmartVerify System",
        description="Officer username or system identifier generating the document"
    )

    class Config:
        arbitrary_types_allowed = True


class PdfGenerationResult(BaseModel):
    """
    Metadata and binary payload returned from successful PDF generation.
    """
    pdf_bytes: bytes = Field(..., description="Raw generated PDF byte stream")
    page_count: int = Field(..., description="Total pages rendered in the document")
    file_size: int = Field(..., description="Size of generated PDF in bytes")
    input_hash: str = Field(..., description="Cryptographic fingerprint of source context")
    template_version: str = Field(..., description="Version of template used for rendering")
    application_id: int = Field(..., description="Application ID")
    generated_at: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat(),
        description="Timestamp of document compilation"
    )

    class Config:
        arbitrary_types_allowed = True


class PdfExportBlockedError(Exception):
    """
    Raised when the Phase 9 validation gate rejects PDF export.
    Carries structured, deterministic diagnostic failure reasons.
    """
    def __init__(self, message: str, gate_reasons: Optional[List[str]] = None):
        super().__init__(message)
        self.message = message
        self.gate_reasons = gate_reasons or []

    def __str__(self) -> str:
        if self.gate_reasons:
            return f"{self.message} Reasons: {'; '.join(self.gate_reasons)}"
        return self.message
