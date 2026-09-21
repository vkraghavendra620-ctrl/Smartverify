"""
Report Template Schema Definitions for SmartVerify Report Generation System.
Defines versioned, machine-readable specifications for report headers, particulars,
footers, and evidence categories.
"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class HeaderField(BaseModel):
    key: str = Field(..., description="Unique field key in header, e.g. 'loan_amount'")
    label: str = Field(..., description="Display label in report header")
    required: bool = Field(True, description="Whether this header field is mandatory")


class HeaderConfig(BaseModel):
    organization_name: str = Field(..., description="Organization or financial institution name")
    report_title: str = Field(..., description="Official title of the report")
    report_subtitle: Optional[str] = Field(None, description="Optional subtitle")
    fields: List[HeaderField] = Field(default_factory=list, description="Ordered header fields")


class ParticularItem(BaseModel):
    id: str = Field(..., description="Stable alphanumeric identifier, e.g. 'P1', 'P2A', 'P7A1'")
    section: str = Field(..., description="Section number or code, e.g. '1', '2', '7'")
    title: str = Field(..., description="Official descriptive title of the particular")
    parent_id: Optional[str] = Field(None, description="ID of parent particular if nested, e.g. 'P2' for 'P2A'")
    applicant_supported: bool = Field(True, description="Whether this row applies to primary applicant")
    guarantor_supported: bool = Field(True, description="Whether this row applies to guarantors / co-applicants")
    data_type: str = Field("text", description="Data type: text, boolean, date, currency, composite, narrative")
    required: bool = Field(True, description="Whether verification detail is required for this particular")
    narrative_allowed: bool = Field(False, description="Whether controlled AI narrative wording is allowed")
    evidence_category: Optional[str] = Field(None, description="Linked evidence category key, e.g. 'applicant_kyc'")
    display_order: int = Field(..., description="Sequential 1-based display ordering")
    description: Optional[str] = Field(None, description="Detailed guidance or sub-clauses for this particular")


class FooterSignatoryBlock(BaseModel):
    key: str = Field(..., description="Unique signatory block key, e.g. 'authorized_signatory'")
    role: str = Field(..., description="Designation / Role, e.g. 'Verification Officer'")
    title: str = Field(..., description="Section title in footer, e.g. 'Branch use / Authorized Signatory'")
    requires_date: bool = Field(True, description="Whether signature requires timestamp/date")
    requires_remarks: bool = Field(False, description="Whether signature includes scrutiny remarks box")


class FooterConfig(BaseModel):
    signatories: List[FooterSignatoryBlock] = Field(default_factory=list)
    confidentiality_notice: str = Field(
        "Confidential Internal Banking Re-Verification Document - SmartVerify System Generated",
        description="Statutory confidentiality footer"
    )


class EvidenceCategory(BaseModel):
    key: str = Field(..., description="Unique evidence category key, e.g. 'applicant_kyc'")
    title: str = Field(..., description="Descriptive title of evidence category")
    description: str = Field(..., description="Scope of documents/screenshots included in this category")
    required: bool = Field(False, description="Whether at least one evidence item is mandatory")


class ReportTemplate(BaseModel):
    template_key: str = Field(..., description="Unique template family key, e.g. 'standard_reverification'")
    template_version: str = Field(..., description="Semantic version string, e.g. 'v1.0'")
    name: str = Field(..., description="Human-readable template name")
    description: str = Field(..., description="Summary of report layout and bank standard compliance")
    header: HeaderConfig = Field(..., description="Header structure specification")
    particulars: List[ParticularItem] = Field(..., description="Ordered list of fixed report particulars")
    footer: FooterConfig = Field(..., description="Footer structure and signature blocks")
    evidence_categories: List[EvidenceCategory] = Field(
        default_factory=list,
        description="Predefined evidence categories for evidence attachment mapping"
    )

    def get_particular_by_id(self, particular_id: str) -> Optional[ParticularItem]:
        for item in self.particulars:
            if item.id == particular_id:
                return item
        return None

    def get_children(self, parent_id: str) -> List[ParticularItem]:
        return [item for item in self.particulars if item.parent_id == parent_id]
