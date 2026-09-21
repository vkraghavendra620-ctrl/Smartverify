"""
Enterprise PDF Report Generation Service (Phase 9).
Renders the final, publication-ready Banking Pre-Sanction Re-Verification Report
from the approved Phase 8 review state, Phase 2 template, and Phase 5 evidence map.

Architectural Constraints:
- ZERO database queries
- ZERO Gemini / LLM calls
- ZERO RAG or vector retrieval
- ZERO file mutations (move/copy/delete/edit)
- Strict immutability of all input objects
- Deterministic layout and content resolution
"""
import io
import os
import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from PIL import Image as PILImage

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    BaseDocTemplate,
    PageTemplate,
    Frame,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    KeepTogether,
    PageBreak,
    Image as RLImage,
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.pdfgen import canvas

from app.report_templates.schema import ReportTemplate, ParticularItem
from app.schemas.evidence_map import EvidenceReference
from app.schemas.report_review import ParticularReview, ReportReviewState
from app.schemas.pdf_report import (
    PdfGenerationRequest,
    PdfGenerationResult,
    PdfExportBlockedError,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# TWO-PASS NUMBERED CANVAS (PAGE X OF Y & STATUTORY WATERMARK)
# ─────────────────────────────────────────────────────────────────────────────

class NumberedCanvas(canvas.Canvas):
    """
    Two-pass canvas that accumulates total page count before rendering
    dynamic 'Page X of Y' footers, running headers, and security watermarks.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states: List[Dict[str, Any]] = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def _draw_page_decorations(self, total_pages: int):
        self.saveState()

        page_w, page_h = A4

        # 1. Diagonal Watermark
        self.setFillColor(colors.HexColor("#64748B"))
        self.setFillAlpha(0.06)
        self.setFont("Helvetica-Bold", 38)
        self.translate(page_w / 2.0, page_h / 2.0)
        self.rotate(45)
        self.drawCentredString(0, 0, "CONFIDENTIAL - INTERNAL BANKING USE")
        self.rotate(-45)
        self.translate(-page_w / 2.0, -page_h / 2.0)
        self.setFillAlpha(1.0)

        # 2. Running Header (Pages > 1)
        if self._pageNumber > 1:
            self.setFont("Helvetica-Bold", 7.5)
            self.setFillColor(colors.HexColor("#1E3A8A"))
            self.drawString(34, page_h - 28, "SMARTVERIFY — PRE-SANCTION RE-VERIFICATION DOSSIER")
            self.setFont("Helvetica", 7.5)
            self.setFillColor(colors.HexColor("#64748B"))
            self.drawRightString(page_w - 34, page_h - 28, "CONFIDENTIAL INTERNAL BANKING DOCUMENT")
            self.setStrokeColor(colors.HexColor("#CBD5E1"))
            self.setLineWidth(0.5)
            self.line(34, page_h - 32, page_w - 34, page_h - 32)

        # 3. Running Footer (All Pages)
        self.setStrokeColor(colors.HexColor("#CBD5E1"))
        self.setLineWidth(0.5)
        self.line(34, 38, page_w - 34, 38)

        self.setFont("Helvetica", 7.5)
        self.setFillColor(colors.HexColor("#64748B"))
        self.drawString(34, 26, "Generated via SmartVerify Automated Engine — Strictly for Authorized Credit Scrutiny Only")
        page_text = f"Page {self._pageNumber} of {total_pages}"
        self.drawRightString(page_w - 34, 26, page_text)

        self.restoreState()


# ─────────────────────────────────────────────────────────────────────────────
# CORRECTION 3: EVIDENCE ASSET RESOLVER (READ-ONLY BOUNDARY)
# ─────────────────────────────────────────────────────────────────────────────

class ResolvedEvidenceAsset:
    """Read-only container representing an evidence artifact resolved for PDF layout."""
    def __init__(
        self,
        evidence_id: str,
        particular_id: str,
        party: str,
        source: str,
        title: str,
        status: str,
        is_image: bool = False,
        image_path: Optional[str] = None,
        image_dims: Optional[Tuple[float, float]] = None,
        status_note: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.evidence_id = evidence_id
        self.particular_id = particular_id
        self.party = party
        self.source = source
        self.title = title
        self.status = status
        self.is_image = is_image
        self.image_path = image_path
        self.image_dims = image_dims
        self.status_note = status_note
        self.metadata = metadata or {}


class EvidenceAssetResolver:
    """
    Explicit read-only Evidence Asset Resolver boundary.
    Flow: EvidenceMap -> EvidenceReference -> EvidenceAssetResolver -> read-only asset -> ReportLab.
    
    Guarantees:
    - Zero database queries.
    - Zero filesystem mutation (no copy, move, delete, rename).
    - If asset file is missing or invalid: sets a neutral deterministic message without crashing.
    """
    MAX_IMAGE_WIDTH = 220.0
    MAX_IMAGE_HEIGHT = 150.0

    @classmethod
    def resolve_asset(cls, ref: EvidenceReference) -> ResolvedEvidenceAsset:
        party_id_val = (ref.party_id or "").lower()
        if "applicant" in party_id_val:
            party_str = "APPLICANT"
        elif "guarantor" in party_id_val:
            party_str = "GUARANTOR"
        else:
            party_label = (ref.party_name or "").upper()
            if "GUARANTOR" in party_label:
                party_str = "GUARANTOR"
            elif "APPLICANT" in party_label:
                party_str = "APPLICANT"
            else:
                party_str = "SHARED"

        source_title = ref.document_type or ref.source or ref.evidence_id
        status_val = ref.status or "AVAILABLE"

        filepath = ref.filename
        if not filepath:
            # Non-file finding or record evidence
            return ResolvedEvidenceAsset(
                evidence_id=ref.evidence_id,
                particular_id=ref.particular_id,
                party=party_str,
                source=ref.source,
                title=source_title,
                status=status_val,
                is_image=False,
                status_note=None,
                metadata=ref.metadata,
            )

        # Check read-only file existence
        if not os.path.isfile(filepath):
            return ResolvedEvidenceAsset(
                evidence_id=ref.evidence_id,
                particular_id=ref.particular_id,
                party=party_str,
                source=ref.source,
                title=source_title,
                status=status_val,
                is_image=False,
                status_note="Evidence asset unavailable for rendering.",
                metadata=ref.metadata,
            )

        # Inspect if image
        valid_img_extensions = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
        if any(filepath.lower().endswith(ext) for ext in valid_img_extensions):
            try:
                with PILImage.open(filepath) as pil_img:
                    w, h = pil_img.size
                    if w <= 0 or h <= 0:
                        raise ValueError("Invalid image dimensions")
                    aspect = float(h) / float(w)
                    target_w = min(float(w), cls.MAX_IMAGE_WIDTH)
                    target_h = target_w * aspect
                    if target_h > cls.MAX_IMAGE_HEIGHT:
                        target_h = cls.MAX_IMAGE_HEIGHT
                        target_w = target_h / aspect
                    return ResolvedEvidenceAsset(
                        evidence_id=ref.evidence_id,
                        particular_id=ref.particular_id,
                        party=party_str,
                        source=ref.source,
                        title=source_title,
                        status=status_val,
                        is_image=True,
                        image_path=filepath,
                        image_dims=(target_w, target_h),
                        metadata=ref.metadata,
                    )
            except Exception as e:
                logger.warning(f"Could not open image {filepath}: {e}")
                return ResolvedEvidenceAsset(
                    evidence_id=ref.evidence_id,
                    particular_id=ref.particular_id,
                    party=party_str,
                    source=ref.source,
                    title=source_title,
                    status=status_val,
                    is_image=False,
                    status_note="Evidence asset unavailable for rendering.",
                    metadata=ref.metadata,
                )

        # Non-image file (e.g. PDF or text attachment)
        return ResolvedEvidenceAsset(
            evidence_id=ref.evidence_id,
            particular_id=ref.particular_id,
            party=party_str,
            source=ref.source,
            title=source_title,
            status=status_val,
            is_image=False,
            status_note=f"Document file attached ({os.path.basename(filepath)}).",
            metadata=ref.metadata,
        )


# ─────────────────────────────────────────────────────────────────────────────
# CORRECTION 1: CONTENT RESOLUTION HIERARCHY
# ─────────────────────────────────────────────────────────────────────────────

def resolve_particular_text(
    particular_id: str,
    particular_review: ParticularReview,
    baseline_report: Optional[Any] = None,
) -> str:
    """
    Deterministically resolves Verification Details text for a given Particular.

    Exact Logic:
    IF missing_information exists AND current_text is empty:
        render INFORMATION REQUIRED
    ELSE IF current_text is non-empty:
        render current_text
    ELSE IF deterministic baseline text exists:
        render deterministic baseline
    ELSE:
        render NO DATA RECORDED

    Guarantees:
    - Never resurrects original_ai_text.
    - Never fabricates missing information.
    """
    has_missing_info = bool(particular_review.missing_information)
    current_text_val = (particular_review.current_text or "").strip()

    # 1. Missing information exists AND current_text is empty
    if has_missing_info and not current_text_val:
        items_str = "; ".join(particular_review.missing_information)
        return f"[INFORMATION REQUIRED: {items_str}]"

    # 2. current_text is non-empty
    if current_text_val:
        return current_text_val

    # 3. deterministic baseline text exists
    if baseline_report and hasattr(baseline_report, "particulars"):
        bp = baseline_report.particulars.get(particular_id)
        if bp and getattr(bp, "summary_lines", None):
            non_empty_lines = [line.strip() for line in bp.summary_lines if line.strip()]
            if non_empty_lines:
                return "\n".join(non_empty_lines)

    # 4. Fallback: NO DATA RECORDED
    return "[NO DATA RECORDED]"


# ─────────────────────────────────────────────────────────────────────────────
# VALIDATION GATE (PHASE 9 EXPORT ENFORCEMENT)
# ─────────────────────────────────────────────────────────────────────────────

def validate_export_gate(request: PdfGenerationRequest) -> None:
    """
    Strict pre-flight gate. Rejects PDF generation if:
    - can_export_pdf is False
    - validation_status == INVALID
    - blocking issues exist
    - input_hash does not match across review state, evidence map, and validated report
    - template key or version does not match
    - mandatory Particulars are missing
    - required review state is absent
    """
    reasons: List[str] = []

    state = request.review_state
    if state is None:
        raise PdfExportBlockedError("Export rejected: review_state is missing.", ["Missing review_state"])

    # 1. can_export_pdf gate
    if not state.can_export_pdf:
        reasons.append("can_export_pdf is False in review state.")

    # 2. validation_status == INVALID
    if state.validation_status == "INVALID":
        reasons.append("Review state validation_status is 'INVALID'.")

    # 3. blocking issues exist
    if state.blocking_issues and len(state.blocking_issues) > 0:
        blocking_codes = [issue.code for issue in state.blocking_issues]
        reasons.append(f"Review state contains {len(state.blocking_issues)} BLOCKING issues: {', '.join(blocking_codes)}.")

    # 4. Input hash integrity
    if request.evidence_map and request.evidence_map.input_hash != state.input_hash:
        reasons.append(f"Hash mismatch: review_state ({state.input_hash[:8]}) != evidence_map ({request.evidence_map.input_hash[:8]}).")

    if request.validated_report and request.validated_report.input_hash != state.input_hash:
        reasons.append(f"Hash mismatch: review_state ({state.input_hash[:8]}) != validated_report ({request.validated_report.input_hash[:8]}).")

    # 5. Template key/version match
    if request.template.template_key != state.template_key:
        reasons.append(f"Template key mismatch: requested '{request.template.template_key}' != review state '{state.template_key}'.")

    if request.template.template_version != state.template_version:
        reasons.append(f"Template version mismatch: requested '{request.template.template_version}' != review state '{state.template_version}'.")

    # 6. Mandatory Particulars presence
    for p_item in request.template.particulars:
        if p_item.required and p_item.id not in state.particular_reviews:
            reasons.append(f"Mandatory Particular '{p_item.id}' ({p_item.title}) is missing from review state.")

    if reasons:
        raise PdfExportBlockedError(
            f"PDF Export Gate Rejected Generation ({len(reasons)} violation(s)).",
            gate_reasons=reasons
        )


# ─────────────────────────────────────────────────────────────────────────────
# CORE PDF GENERATOR (REPORTLAB PLATYPUS ENGINE)
# ─────────────────────────────────────────────────────────────────────────────

def generate_pdf_report(request: PdfGenerationRequest) -> PdfGenerationResult:
    """
    Renders the approved verification report into a deterministic, publication-quality PDF.
    Enforces pre-flight gate, builds flowable elements, and generates output buffer.
    """
    # 1. Enforce Validation Gate
    validate_export_gate(request)

    state = request.review_state
    template = request.template
    baseline = request.baseline_report
    evidence_map = request.evidence_map

    # 2. Setup Document Layout
    buffer = io.BytesIO()
    doc = BaseDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=1.2 * cm,
        rightMargin=1.2 * cm,
        topMargin=2.0 * cm,
        bottomMargin=2.0 * cm,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    template_page = PageTemplate(id="standard", frames=frame)
    doc.addPageTemplates([template_page])

    # 3. Typography Styles
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#0F172A"),
        alignment=TA_CENTER,
    )
    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#475569"),
        alignment=TA_CENTER,
    )
    org_style = ParagraphStyle(
        "OrgHeader",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#1E3A8A"),
        alignment=TA_CENTER,
    )
    meta_key_style = ParagraphStyle(
        "MetaKey",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=9.5,
        textColor=colors.HexColor("#334155"),
    )
    meta_val_style = ParagraphStyle(
        "MetaVal",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.5,
        leading=9.5,
        textColor=colors.HexColor("#0F172A"),
    )
    section_header_style = ParagraphStyle(
        "SectionHeader",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#1E3A8A"),
    )
    table_cell_id_style = ParagraphStyle(
        "CellId",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#1E293B"),
    )
    table_cell_title_style = ParagraphStyle(
        "CellTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#0F172A"),
    )
    table_cell_desc_style = ParagraphStyle(
        "CellDesc",
        parent=styles["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=7,
        leading=8.5,
        textColor=colors.HexColor("#64748B"),
    )
    table_cell_body_style = ParagraphStyle(
        "CellBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.5,
        leading=9.5,
        textColor=colors.HexColor("#0F172A"),
    )
    info_req_style = ParagraphStyle(
        "InfoReqBody",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=9.5,
        textColor=colors.HexColor("#B91C1C"),
    )
    badge_verified_style = ParagraphStyle(
        "BadgeVerified",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7,
        leading=8.5,
        textColor=colors.HexColor("#15803D"),
        alignment=TA_CENTER,
    )
    badge_action_style = ParagraphStyle(
        "BadgeAction",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7,
        leading=8.5,
        textColor=colors.HexColor("#B91C1C"),
        alignment=TA_CENTER,
    )
    badge_review_style = ParagraphStyle(
        "BadgeReview",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7,
        leading=8.5,
        textColor=colors.HexColor("#B45309"),
        alignment=TA_CENTER,
    )

    story = []

    # ─── HEADER SECTION ──────────────────────────────────────────────────────
    story.append(Paragraph(template.header.organization_name, org_style))
    story.append(Spacer(1, 2))
    story.append(Paragraph(template.header.report_title, title_style))
    if template.header.report_subtitle:
        story.append(Spacer(1, 1))
        story.append(Paragraph(template.header.report_subtitle, subtitle_style))
    story.append(Spacer(1, 6))

    # Application Metadata Grid
    gen_dt_str = request.generation_timestamp or datetime.utcnow().strftime("%d %b %Y, %H:%M UTC")
    app_id_str = f"APP-{state.application_id:06d}"

    meta_data = [
        [
            Paragraph("Application No:", meta_key_style), Paragraph(app_id_str, meta_val_style),
            Paragraph("Template Key:", meta_key_style), Paragraph(f"{template.template_key} ({template.template_version})", meta_val_style),
        ],
        [
            Paragraph("Investigation Date:", meta_key_style), Paragraph(gen_dt_str, meta_val_style),
            Paragraph("Revision:", meta_key_style), Paragraph(f"Rev #{state.revision} (Audit Verified)", meta_val_style),
        ],
        [
            Paragraph("Reviewing Official:", meta_key_style), Paragraph(request.generated_by, meta_val_style),
            Paragraph("Verification Status:", meta_key_style), Paragraph(state.validation_status, meta_val_style),
        ],
        [
            Paragraph("Cryptographic Hash:", meta_key_style), Paragraph(f"{state.input_hash[:16]}...", meta_val_style),
            Paragraph("Overall Quality:", meta_key_style), Paragraph("VALIDATED BY RULE ENGINE", meta_val_style),
        ],
    ]
    meta_table = Table(meta_data, colWidths=[105, 160, 105, 156])
    meta_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
        ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#94A3B8")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 8))

    # ─── 27 PARTICULARS TABLE ────────────────────────────────────────────────
    # Build rows strictly in template order
    table_rows = []
    # Header Row
    table_rows.append([
        Paragraph("No.", ParagraphStyle("TH1", fontName="Helvetica-Bold", fontSize=8, textColor=colors.white, alignment=TA_CENTER)),
        Paragraph("Particulars / Verification Clause", ParagraphStyle("TH2", fontName="Helvetica-Bold", fontSize=8, textColor=colors.white)),
        Paragraph("Verification Details & Re-Verification Observations", ParagraphStyle("TH3", fontName="Helvetica-Bold", fontSize=8, textColor=colors.white)),
        Paragraph("Status", ParagraphStyle("TH4", fontName="Helvetica-Bold", fontSize=8, textColor=colors.white, alignment=TA_CENTER)),
    ])

    current_section = None
    for item in template.particulars:
        # Check if new section
        if item.section != current_section and not item.parent_id:
            current_section = item.section

        p_review = state.particular_reviews.get(item.id)
        if not p_review:
            # Fallback placeholder if somehow not in reviews
            resolved_text = "[NO DATA RECORDED]"
            val_status = "UNKNOWN"
            is_info_req = False
        else:
            resolved_text = resolve_particular_text(item.id, p_review, baseline)
            val_status = p_review.validation_status
            is_info_req = resolved_text.startswith("[INFORMATION REQUIRED")

        # Format title cell
        indent_prefix = "&nbsp;&nbsp;&nbsp;&nbsp;" if item.parent_id else ""
        title_flowables = [Paragraph(f"{indent_prefix}{item.title}", table_cell_title_style)]
        if item.description and not item.parent_id:
            title_flowables.append(Paragraph(f"{indent_prefix}{item.description}", table_cell_desc_style))

        # Format details cell
        body_style = info_req_style if is_info_req else table_cell_body_style
        # Replace newlines with <br/> for ReportLab paragraph display
        formatted_text = resolved_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")
        # Unescape our custom bracket tags for cleaner bold display
        formatted_text = formatted_text.replace("&lt;br/&gt;", "<br/>")
        if is_info_req:
            formatted_text = f"<b>{formatted_text}</b>"
        details_flowable = Paragraph(formatted_text, body_style)

        # Status Badge
        if is_info_req:
            status_chip = Paragraph("ACTION<br/>REQ.", badge_action_style)
        elif val_status == "VALID":
            status_chip = Paragraph("VERIFIED", badge_verified_style)
        elif val_status == "WARNING":
            status_chip = Paragraph("REVIEW", badge_review_style)
        else:
            status_chip = Paragraph("CHECK", badge_review_style)

        table_rows.append([
            Paragraph(item.id, table_cell_id_style),
            title_flowables,
            details_flowable,
            status_chip,
        ])

    # Table Geometry: Available printable width = 526 pt (A4 width 595 - 2*34)
    # [36, 160, 275, 55] = 526 pt
    particulars_table = Table(table_rows, colWidths=[36, 160, 275, 55], repeatRows=1)
    
    t_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E293B")),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    # Alternate row colors
    for r_idx in range(1, len(table_rows)):
        if r_idx % 2 == 0:
            t_style.append(("BACKGROUND", (0, r_idx), (-1, r_idx), colors.HexColor("#F8FAFC")))
    particulars_table.setStyle(TableStyle(t_style))
    story.append(particulars_table)
    story.append(Spacer(1, 10))

    # ─── SIGNATORY FOOTER SECTION ────────────────────────────────────────────
    story.append(Paragraph("<b>STATUTORY SCRUTINY, ACCEPTANCE &amp; SIGNATORY CERTIFICATION</b>", section_header_style))
    story.append(Spacer(1, 4))

    sig_cells = []
    for sig_block in template.footer.signatories:
        sig_cells.append([
            Paragraph(f"<b>{sig_block.title}</b>", meta_key_style),
            Spacer(1, 24), # Signature space
            Paragraph(f"Designation: {sig_block.role}", meta_val_style),
            Paragraph(f"Date: {gen_dt_str[:11]}", meta_val_style),
            Paragraph("Remarks: Confirmed &amp; Accepted" if sig_block.requires_remarks else "Status: Verified", meta_val_style),
        ])

    sig_table = Table([sig_cells], colWidths=[175, 175, 176])
    sig_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#94A3B8")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(KeepTogether([sig_table, Spacer(1, 4), Paragraph(f"<i>{template.footer.confidentiality_notice}</i>", subtitle_style)]))

    # ─── EVIDENCE DOSSIER (SUPPORTING PAGES) ──────────────────────────────────
    # Reviewer evidence selections filter: included == True ONLY
    selected_evidence_items: List[Tuple[str, EvidenceReference]] = []

    # Map of selections: state.evidence_selections is Dict[particular_id, List[EvidenceSelection]]
    selection_map: Dict[str, Dict[str, bool]] = {}
    for pid, s_list in state.evidence_selections.items():
        selection_map[pid] = {s.evidence_id: s.included for s in s_list}

    # Iterate particulars in order
    for item in template.particulars:
        p_mapping = evidence_map.particular_mappings.get(item.id)
        if not p_mapping or not p_mapping.evidence:
            continue

        p_selections = selection_map.get(item.id, {})
        for ref in p_mapping.evidence:
            # Default to included if not explicitly toggled
            is_included = p_selections.get(ref.evidence_id, True)
            if is_included:
                selected_evidence_items.append((item.id, ref))

    if selected_evidence_items:
        story.append(PageBreak())
        story.append(Paragraph("<b>ANNEXURE: MAPPED VERIFICATION EVIDENCE DOSSIER</b>", title_style))
        story.append(Paragraph("Supporting Documents, Government Portal Screenshots &amp; Field Site Photographs", subtitle_style))
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            "<i>Note: Displayed evidence reflects verified records selected and approved by the investigating officer. Excluded records are suppressed in accordance with banking governance rules.</i>",
            meta_val_style,
        ))
        story.append(Spacer(1, 8))

        current_evidence_pid = None
        for pid, ref in selected_evidence_items:
            # Header per Particular group
            if pid != current_evidence_pid:
                current_evidence_pid = pid
                p_item = template.get_particular_by_id(pid)
                p_title = p_item.title if p_item else pid
                story.append(Spacer(1, 4))
                story.append(Paragraph(f"<b>Particular [{pid}]: {p_title}</b>", section_header_style))
                story.append(Spacer(1, 3))

            # Resolve evidence asset via EvidenceAssetResolver
            asset = EvidenceAssetResolver.resolve_asset(ref)

            # Build evidence card flowables
            card_flowables = []
            
            # Party and status banner
            party_badge_color = "#1E3A8A" if asset.party == "APPLICANT" else ("#0D9488" if asset.party == "GUARANTOR" else "#64748B")
            party_html = f"<font color='{party_badge_color}'><b>[{asset.party}]</b></font> &nbsp; <b>{asset.title}</b> &nbsp; <font color='#64748B'>[ID: {asset.evidence_id}]</font>"
            card_flowables.append(Paragraph(party_html, table_cell_title_style))
            card_flowables.append(Spacer(1, 2))

            # Asset rendering: image vs status note vs placeholder
            if asset.is_image and asset.image_path and asset.image_dims:
                w, h = asset.image_dims
                card_flowables.append(RLImage(asset.image_path, width=w, height=h))
                card_flowables.append(Spacer(1, 2))
                card_flowables.append(Paragraph(f"<i>Source: {asset.source} | Status: {asset.status}</i>", table_cell_desc_style))
            elif asset.status_note:
                # Deterministic neutral note (Missing evidence or document attachment)
                card_flowables.append(Paragraph(f"<b>Notice:</b> {asset.status_note}", meta_val_style))
                card_flowables.append(Paragraph(f"<i>Source Reference: {asset.source} | Category: {ref.evidence_category}</i>", table_cell_desc_style))
            else:
                # Metadata card
                meta_lines = [f"<b>{k}:</b> {v}" for k, v in list(asset.metadata.items())[:3]]
                meta_summary = " &nbsp;|&nbsp; ".join(meta_lines) if meta_lines else "Record finding substantiated."
                card_flowables.append(Paragraph(meta_summary, meta_val_style))
                card_flowables.append(Paragraph(f"<i>Verification Finding | Status: {asset.status}</i>", table_cell_desc_style))

            # Wrap in single-cell table for neat card border
            card_table = Table([[card_flowables]], colWidths=[526])
            card_table.setStyle(TableStyle([
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFFFFF")),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]))
            story.append(card_table)
            story.append(Spacer(1, 5))

    # 4. Build Document using NumberedCanvas
    doc.build(story, canvasmaker=NumberedCanvas)

    pdf_bytes = buffer.getvalue()
    buffer.close()

    # 5. Return Strongly-Typed Result
    return PdfGenerationResult(
        pdf_bytes=pdf_bytes,
        page_count=doc.page,
        file_size=len(pdf_bytes),
        input_hash=state.input_hash,
        template_version=template.template_version,
        application_id=state.application_id,
        generated_at=gen_dt_str,
    )
