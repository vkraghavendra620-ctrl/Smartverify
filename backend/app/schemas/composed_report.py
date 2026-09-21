"""
Composed Report Schemas for SmartVerify Report Generation System (Phase 6).
Defines strongly-typed, machine-readable representations of controlled AI report
composition, deterministic fact preservation, and explicit provenance tracking.
"""
from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


class ComposedParticular(BaseModel):
    """
    Individual composed output for a single report Particular.
    Tracks composed or deterministic text, explicit fact and evidence provenance,
    confidence metrics, and missing information.
    """
    particular_id: str = Field(..., description="Stable Particular identifier, e.g. 'P1', 'P2A', 'P4B'")
    status: str = Field(
        ...,
        description="Status: 'composed', 'deterministic', 'information_required', 'composer_unavailable', 'skipped'"
    )
    text: str = Field("", description="Factual narrative or deterministically rendered verification text")
    fact_ids: List[str] = Field(default_factory=list, description="Associated ReportContext fact IDs (provenance)")
    evidence_ids: List[str] = Field(default_factory=list, description="Associated EvidenceReference IDs (traceability)")
    confidence: Optional[float] = Field(None, description="Confidence score inherited from finding or model")
    missing_information: List[str] = Field(
        default_factory=list,
        description="Explicit list of required fields/evidence missing from context"
    )
    generated_at: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat(),
        description="ISO-8601 UTC timestamp of composition"
    )
    model: Optional[str] = Field(None, description="Model identifier used for composition (e.g. 'gemini-2.5-flash')")
    prompt_version: Optional[str] = Field(None, description="Version of the prompt used (e.g. 'v1.0')")


class ComposedReport(BaseModel):
    """
    Complete, strongly-typed Composed Report (Phase 6).
    Combines deterministic fact preservation with controlled AI wording for narrative particulars.
    Contains zero fabricated claims, zero unmasked PII, and full fact/evidence provenance.
    """
    application_id: int = Field(..., description="Unique application identifier")
    template_key: str = Field(..., description="Template key from Phase 2, e.g. 'standard_reverification'")
    template_version: str = Field(..., description="Template version, e.g. 'v1.0'")
    particular_outputs: Dict[str, ComposedParticular] = Field(
        default_factory=dict,
        description="Dictionary of ComposedParticular keyed by stable Particular ID"
    )
    input_hash: str = Field(..., description="SHA-256 fingerprint carried forward from Phase 3 ReportContext")
    composer_version: str = Field("v1.0.0", description="Semantic version of the AI Report Composer service")
    total_composed_count: int = Field(0, description="Total number of particulars composed via controlled AI wording")
    total_deterministic_count: int = Field(0, description="Total number of particulars rendered deterministically")
    missing_information_count: int = Field(0, description="Total count of particulars flagged with information_required")

    def get_particular(self, particular_id: str) -> Optional[ComposedParticular]:
        """Retrieve output for a specific Particular ID."""
        return self.particular_outputs.get(particular_id)

    def get_text(self, particular_id: str) -> str:
        """Retrieve the text string for a specific Particular ID."""
        part = self.particular_outputs.get(particular_id)
        return part.text if part else ""
