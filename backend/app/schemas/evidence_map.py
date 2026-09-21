"""
Evidence Map Schemas for SmartVerify Report Generation System.
Defines typed, machine-readable representations of deterministic evidence traceability
linking verified database facts, documents, government screenshots, and findings
to report Particulars according to the Phase 2 ReportTemplate.
"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class EvidenceReference(BaseModel):
    """
    Deterministic reference to an existing evidence artifact
    (document, government screenshot, or finding) without file duplication.
    """
    evidence_id: str = Field(..., description="Stable unique identifier, e.g. 'doc:12', 'gov_shot:3', 'finding:5'")
    evidence_type: str = Field(..., description="'document', 'government_screenshot', 'finding'")
    source: str = Field(..., description="'documents', 'government_verification_screenshots', 'application_findings'")
    source_id: str = Field(..., description="Original source record identifier, e.g. '12', '3'")
    filename: Optional[str] = Field(None, description="Original filename or screenshot path")
    document_type: Optional[str] = Field(None, description="Document type, verification type, or finding category")
    party_id: Optional[str] = Field(None, description="Party ID: 'applicant', 'guarantor:1', or None for shared")
    party_name: Optional[str] = Field(None, description="Resolved legal name of the associated party")
    particular_id: str = Field(..., description="Target report Particular ID, e.g. 'P1', 'P3', 'P7A1'")
    evidence_category: str = Field(..., description="Phase 2 template evidence category key, e.g. 'applicant_kyc'")
    status: str = Field("available", description="Verification or document status, e.g. 'available', 'VERIFIED'")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Traceable metadata payload (strictly no binary files)")


class EvidenceMapping(BaseModel):
    """
    Deterministic evidence container for a single report Particular.
    Segregates evidence across Applicant, Guarantor, and Shared categories.
    """
    particular_id: str = Field(..., description="Target report Particular ID, e.g. 'P1', 'P7A1'")
    evidence_category: Optional[str] = Field(None, description="Template evidence category key")
    evidence: List[EvidenceReference] = Field(default_factory=list, description="All deduplicated and deterministically ordered references")
    applicant_evidence: List[EvidenceReference] = Field(default_factory=list, description="Evidence references specific to primary applicant")
    guarantor_evidence: List[EvidenceReference] = Field(default_factory=list, description="Evidence references specific to guarantors/co-applicants")
    shared_evidence: List[EvidenceReference] = Field(default_factory=list, description="Shared, asset, site, or general evidence references")
    evidence_missing: bool = Field(False, description="Whether required/expected evidence for this particular is missing")
    missing_reason: Optional[str] = Field(None, description="Descriptive explanation when evidence is missing")


class EvidenceMap(BaseModel):
    """
    Complete, typed, machine-readable Evidence Map (Phase 5).
    Deterministically constructed from ReportContext and Phase 2 ReportTemplate.
    Zero AI ranking, zero semantic similarity, and zero file mutation.
    """
    template_key: str = Field(..., description="Report template family key, e.g. 'standard_reverification'")
    template_version: str = Field(..., description="Report template version, e.g. 'v1.0'")
    application_id: int = Field(..., description="Unique application identifier")
    particular_mappings: Dict[str, EvidenceMapping] = Field(
        default_factory=dict,
        description="Dictionary of EvidenceMapping keyed by stable Particular ID"
    )
    input_hash: str = Field(..., description="SHA-256 fingerprint inherited from ReportContext for staleness verification")
    total_evidence_count: int = Field(0, description="Total count of unique evidence references across all particulars")
    missing_evidence_particular_ids: List[str] = Field(
        default_factory=list,
        description="List of Particular IDs where required evidence is missing"
    )

    def get_mapping(self, particular_id: str) -> Optional[EvidenceMapping]:
        """Convenience method to retrieve evidence mapping for a particular."""
        return self.particular_mappings.get(particular_id)

    def get_evidence_for_particular(self, particular_id: str) -> List[EvidenceReference]:
        """Convenience method to retrieve the complete evidence list for a particular."""
        mapping = self.particular_mappings.get(particular_id)
        if mapping:
            return mapping.evidence
        return []
