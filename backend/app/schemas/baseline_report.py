"""
Baseline Report Schema for SmartVerify Report Generation System.
Defines typed, machine-readable representations of the deterministic,
template-rendered baseline report (Phase 4).
"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

from app.schemas.report_context import (
    ReportMetadata,
    NormalizedParty,
    NormalizedDocumentMeta,
    GovernmentScreenshotMeta,
    NormalizedFinding,
    MissingParticularInfo,
)
from app.report_templates.schema import FooterSignatoryBlock


class RenderedPartyDetails(BaseModel):
    """Normalized and deterministically formatted party identification block."""
    party_id: str
    party_type: str  # 'applicant', 'co_applicant', 'guarantor'
    name: Optional[str] = None
    father_name: Optional[str] = None
    address: Optional[str] = None
    mobile: Optional[str] = None
    email: Optional[str] = None
    dob: Optional[str] = None
    masked_aadhaar: Optional[str] = None
    pan_number: Optional[str] = None
    occupation: Optional[str] = None
    years_in_occupation: Optional[int] = None
    formatted_income: Optional[str] = None
    income_period: Optional[str] = None
    relationship_to_applicant: Optional[str] = None


class RenderedPartiesSummary(BaseModel):
    """Summary collection of parties present in the application."""
    applicant: RenderedPartyDetails
    guarantors: List[RenderedPartyDetails] = Field(default_factory=list)


class RenderedVerificationDetail(BaseModel):
    """
    Rendered verification content for a specific column (Applicant or Guarantor)
    or shared across the particular.
    """
    party_id: Optional[str] = None
    party_type: Optional[str] = None
    party_name: Optional[str] = None
    formatted_facts: Dict[str, Any] = Field(default_factory=dict)
    summary_lines: List[str] = Field(default_factory=list)
    status: str = "available"  # 'available', 'partial', 'missing', 'not_applicable'
    missing_marker: Optional[str] = None


class RenderedParticular(BaseModel):
    """
    A single rendered report row corresponding to a fixed ParticularItem
    from the Phase 2 template.
    """
    id: str = Field(..., description="Stable Particular ID, e.g. 'P1', 'P2A', 'P7A1'")
    section: str = Field(..., description="Section number: '1' through '8'")
    title: str = Field(..., description="Official Particular title from template")
    parent_id: Optional[str] = Field(None, description="Parent Particular ID if nested")
    display_order: int = Field(..., description="1-based contiguous display sequence")
    applicant_supported: bool
    guarantor_supported: bool
    data_type: str
    required: bool
    narrative_allowed: bool
    evidence_category: Optional[str] = None

    # Deterministically rendered party columns
    applicant_verification: Optional[RenderedVerificationDetail] = None
    guarantor_verifications: List[RenderedVerificationDetail] = Field(default_factory=list)
    shared_verification: Optional[RenderedVerificationDetail] = None

    # Missing information tracking
    missing: bool = False
    missing_fields: List[str] = Field(default_factory=list)
    missing_marker: Optional[str] = None  # 'INFORMATION REQUIRED' when required & missing
    status: str = "available"

    # Underlying structured facts for audit/provenance
    raw_facts: Dict[str, Any] = Field(default_factory=dict)


class RenderedSection(BaseModel):
    """Logical grouping of rendered particulars by section."""
    section_number: str
    section_title: str
    particulars: List[RenderedParticular] = Field(default_factory=list)


class RenderedHeader(BaseModel):
    """Structured header specification for report display."""
    organization_name: str
    report_title: str
    report_subtitle: Optional[str] = None
    application_id: int
    application_number: str
    borrower_name: Optional[str] = None
    loan_type: Optional[str] = None
    raw_loan_amount: float
    formatted_loan_amount: str
    branch: str
    report_date: str
    officer_name: Optional[str] = None


class RenderedFooter(BaseModel):
    """Structured footer specification including signatories and statutory notices."""
    signatories: List[FooterSignatoryBlock] = Field(default_factory=list)
    confidentiality_notice: str
    officer_name: Optional[str] = None
    branch_name: Optional[str] = None
    report_date: str


class BaselineReport(BaseModel):
    """
    Complete, typed, machine-readable Baseline Report (Phase 4).
    Deterministically rendered from ReportContext and ReportTemplate.
    Contains zero AI-generated prose and zero speculative values.
    """
    template_key: str
    template_version: str
    header: RenderedHeader
    parties: RenderedPartiesSummary
    particulars: List[RenderedParticular] = Field(default_factory=list)
    sections: List[RenderedSection] = Field(default_factory=list)
    footer: RenderedFooter
    missing_information: List[MissingParticularInfo] = Field(default_factory=list)

    # Exposed raw context metadata (strictly without evidence ranking/selection)
    documents: List[NormalizedDocumentMeta] = Field(default_factory=list)
    government_screenshots: List[GovernmentScreenshotMeta] = Field(default_factory=list)
    findings: List[NormalizedFinding] = Field(default_factory=list)

    # Deterministic fingerprint preserved from Phase 3 ReportContext
    input_hash: str

    def get_particular(self, particular_id: str) -> Optional[RenderedParticular]:
        """Convenience method to retrieve a rendered particular by ID."""
        for p in self.particulars:
            if p.id == particular_id:
                return p
        return None
