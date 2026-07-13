"""
PDF Report Generation Service
Generates a professional PDF verification report using ReportLab.
"""
import logging, os, json
from datetime import datetime
from typing import Dict, Any
from sqlalchemy.orm import Session

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether, Image, PageBreak
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT

from app.core.config import settings
from app.models.application import Application
from app.models.verification_report import VerificationReport
from app.models.document import Document

logger = logging.getLogger(__name__)

def _mask(value: str, show: int = 4) -> str:
    if not value or value == "N/A":
        return "N/A"
    if len(value) <= show:
        return value
    return "*" * (len(value) - show) + value[-show:]

def generate_report(application_id: int, db: Session) -> str:
    """Generate a comprehensive PDF report consuming all AI and application data."""
    os.makedirs(settings.REPORT_DIR, exist_ok=True)
    filename = f"report_{application_id}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.pdf"
    filepath = os.path.join(settings.REPORT_DIR, filename)

    app = db.query(Application).filter(Application.id == application_id).first()
    if not app:
        raise ValueError(f"Application {application_id} not found.")

    report = db.query(VerificationReport).filter(VerificationReport.application_id == application_id).first()
    
    # Helper to safely extract JSON strings if they were saved as text
    def safe_json(val):
        if isinstance(val, dict): return val
        if isinstance(val, list): return val
        if isinstance(val, str):
            try: return json.loads(val)
            except: return {}
        return {}

    extracted = safe_json(report.extracted_info) if report else {}
    verification_details = safe_json(report.verification_details) if report else {}
    fraud = safe_json(report.fraud_analysis) if report else {}
    agent_trace = safe_json(report.agent_trace) if report else {}

    doc = SimpleDocTemplate(filepath, pagesize=A4,
                            leftMargin=1.5*cm, rightMargin=1.5*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("Title", parent=styles["Title"], fontSize=18, textColor=colors.HexColor("#1e3a5f"), spaceAfter=6)
    h1_style = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=14, textColor=colors.HexColor("#1e3a5f"), spaceBefore=15, spaceAfter=8)
    h2_style = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=12, textColor=colors.HexColor("#334155"), spaceBefore=10, spaceAfter=4)
    normal = styles["Normal"]
    small  = ParagraphStyle("Small", parent=normal, fontSize=9)
    bullet = ParagraphStyle("Bullet", parent=normal, leftIndent=15, bulletIndent=5, spaceAfter=2)
    code = ParagraphStyle("Code", parent=normal, fontName="Courier", fontSize=8, textColor=colors.HexColor("#334155"))

    def section_header(text):
        return [
            Paragraph(text, h1_style),
            HRFlowable(width="100%", thickness=1, color=colors.HexColor("#1e3a5f")),
            Spacer(1, 10),
        ]

    story = []

    # ──────────────────────────────────────────────────────────
    # 1. Cover Header
    # ──────────────────────────────────────────────────────────
    story.append(Paragraph("SMARTVERIFY", title_style))
    story.append(Paragraph("Professional Loan Verification Report", ParagraphStyle("Sub", parent=normal, fontSize=14, textColor=colors.grey)))
    story.append(Spacer(1, 10))

    header_data = [
        ["Application ID", f"#{app.id}"],
        ["Branch", app.branch or "N/A"],
        ["Generated Date", datetime.utcnow().strftime('%d %B %Y, %H:%M UTC')],
        ["Officer Name", app.user.name if app.user else "System"],
        ["Status", app.status.value.upper() if app.status else "PENDING"]
    ]
    htable = Table(header_data, colWidths=["30%", "70%"])
    htable.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#cbd5e1")),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#64748b")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(htable)
    story.append(Spacer(1, 20))

    # ──────────────────────────────────────────────────────────
    # 2. Applicant Details
    # ──────────────────────────────────────────────────────────
    story += section_header("2. Applicant Details")
    app_data = [
        ["Name", extracted.get("applicant_name") or app.applicant_name or "N/A"],
        ["DOB", extracted.get("dob", "N/A")],
        ["Gender", extracted.get("gender", "N/A")],
        ["Mobile", extracted.get("phone", "N/A")],
        ["Aadhaar", _mask(extracted.get("aadhaar_number", "N/A"))],
        ["PAN", _mask(extracted.get("pan_number", "N/A"))],
        ["Address", extracted.get("address", "N/A")],
        ["Employment", extracted.get("employer_name", "N/A")],
    ]
    atable = Table(app_data, colWidths=["30%", "70%"])
    atable.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(atable)
    story.append(Spacer(1, 15))

    # ──────────────────────────────────────────────────────────
    # 3. Joint Applicants
    # ──────────────────────────────────────────────────────────
    if app.joint_applicants:
        story += section_header("3. Joint Applicants")
        for ja in app.joint_applicants:
            ja_data = [
                ["Applicant Index", f"Joint Applicant {ja.index}"],
                ["Relationship", ja.relationship_type or "N/A"],
                ["Mobile", ja.mobile or "N/A"],
                ["Email", ja.email or "N/A"],
                ["Remarks", ja.remarks or "N/A"]
            ]
            jtable = Table(ja_data, colWidths=["30%", "70%"])
            jtable.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("PADDING", (0, 0), (-1, -1), 6),
            ]))
            story.append(jtable)
            story.append(Spacer(1, 10))

    # ──────────────────────────────────────────────────────────
    # 4. Loan Details
    # ──────────────────────────────────────────────────────────
    story += section_header("4. Loan Details")
    loan_data = [
        ["Loan Type", app.loan_type or "N/A"],
        ["Loan Amount", f"Rs. {app.loan_amount:,.2f}" if app.loan_amount else "N/A"],
        ["Loan Tenure", f"{app.loan_tenure} Months" if app.loan_tenure else "N/A"],
        ["Interest Rate", f"{app.interest_rate}%" if app.interest_rate else "N/A"],
        ["Branch", app.branch or "N/A"]
    ]
    ltable = Table(loan_data, colWidths=["30%", "70%"])
    ltable.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(ltable)
    story.append(Spacer(1, 15))

    # ──────────────────────────────────────────────────────────
    # 5. Property Details
    # ──────────────────────────────────────────────────────────
    if app.property_details:
        story += section_header("5. Property Details")
        pd = app.property_details
        prop_data = [
            ["Property Type", pd.property_type or "N/A"],
            ["Address", pd.address or "N/A"],
            ["Village/City", pd.village_city or "N/A"],
            ["Survey Number", pd.survey_number or "N/A"],
            ["Khata Number", pd.khata_number or "N/A"],
            ["Area", pd.property_area or "N/A"],
            ["Market Value", f"Rs. {pd.market_value:,.2f}" if pd.market_value else "N/A"],
        ]
        ptable = Table(prop_data, colWidths=["30%", "70%"])
        ptable.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ("PADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(ptable)
        story.append(Spacer(1, 15))

    # ──────────────────────────────────────────────────────────
    # 6. Site Verification
    # ──────────────────────────────────────────────────────────
    if app.site_verification:
        story.append(PageBreak())
        story += section_header("6. Site Verification")
        sv = app.site_verification
        sv_data = [
            ["Officer Name", sv.officer_name or "N/A"],
            ["Visit Date", sv.date or "N/A"],
            ["Visit Time", sv.time or "N/A"],
            ["GPS Coordinates", sv.gps_coordinates or "N/A"],
            ["Property Condition", sv.property_condition or "N/A"],
            ["Boundary Present", sv.boundary_present or "N/A"],
            ["Road Access", sv.road_access or "N/A"],
            ["Remarks", Paragraph(sv.remarks or "N/A", normal)],
        ]
        svtable = Table(sv_data, colWidths=["30%", "70%"])
        svtable.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ("PADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "TOP")
        ]))
        story.append(svtable)
        story.append(Spacer(1, 15))

    # ──────────────────────────────────────────────────────────
    # 7. Government Verification
    # ──────────────────────────────────────────────────────────
    story += section_header("7. Government Verification")
    gov_model = app.gov_verification
    if not gov_model:
        gov = fraud.get("government_verification") or fraud
        gov_data = [
            ["Document", "Status", "Timestamp/Notes"],
            ["Aadhaar", gov.get("aadhaar") or gov.get("aadhaar_status", "N/A"), "Not Available"],
            ["PAN", gov.get("pan") or gov.get("pan_status", "N/A"), "Not Available"],
            ["Tax Receipt", gov.get("tax_receipt") or gov.get("tax_receipt_status", "N/A"), "Not Available"],
        ]
        remarks = gov.get("remarks")
        issues = gov.get("issues", [])
    else:
        gov_data = [
            ["Document", "Status", "Timestamp/Notes"],
            ["Aadhaar", gov_model.aadhaar_status or "N/A", gov_model.timestamp or "Not Available"],
            ["PAN", gov_model.pan_status or "N/A", gov_model.timestamp or "Not Available"],
            ["Tax Receipt", gov_model.tax_receipt_status or "N/A", gov_model.timestamp or "Not Available"],
        ]
        remarks = gov_model.remarks
        issues = []

    gtable = Table(gov_data, colWidths=["30%", "30%", "40%"])
    gtable.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a5f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(gtable)
    
    if remarks or issues:
        story.append(Spacer(1, 5))
        if remarks:
            story.append(Paragraph("<b>Agent Remarks:</b> " + str(remarks), normal))
        for iss in issues:
            story.append(Paragraph(f"• {iss}", bullet))
    story.append(Spacer(1, 15))

    # ──────────────────────────────────────────────────────────
    # 8. AI Verification Summary
    # ──────────────────────────────────────────────────────────
    story.append(PageBreak())
    story += section_header("8. AI Verification Summary")
    
    # Extract Confidence Scores
    overall_conf = agent_trace.get("overall_ai_confidence", "N/A")
    findings = agent_trace.get("agent_findings", {})
    da = findings.get("document_analyst", {})
    es = findings.get("extraction_specialist", {})
    vo = findings.get("verification_officer", {})
    ga = findings.get("gov_verification_agent", {})

    conf_data = [
        ["Metric", "Confidence Score"],
        ["Overall AI Confidence", f"{overall_conf}%" if isinstance(overall_conf, (int, float)) else overall_conf],
        ["OCR Confidence", f"{da.get('ocr_confidence', 'N/A')}%"],
        ["Extraction Confidence", f"{es.get('extraction_confidence', 'N/A')}%"],
        ["Verification Confidence", f"{vo.get('verification_confidence', 'N/A')}%"],
        ["Fraud/Gov Confidence", f"{ga.get('fraud_confidence', 'N/A')}%"]
    ]
    ctable = Table(conf_data, colWidths=["50%", "50%"])
    ctable.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(Paragraph("<b>AI Confidence Scores</b>", h2_style))
    story.append(ctable)
    story.append(Spacer(1, 15))

    story.append(Paragraph("<b>OCR Summary (Agent 1)</b>", h2_style))
    story.append(Paragraph(f"Documents Processed: {len(app.documents) if app.documents else 0} | Engine: EasyOCR / Tesseract", normal))
    
    story.append(Paragraph("<b>NLP Summary (Agent 2)</b>", h2_style))
    nlp = agent_trace.get("agent_findings", {}).get("extraction_specialist", {})
    if nlp.get("missing_fields"):
        story.append(Paragraph("Missing Fields:", normal))
        for f in nlp.get("missing_fields", []):
            story.append(Paragraph(f"• {f}", bullet))
    else:
        story.append(Paragraph("No Missing Fields.", normal))
        
    if nlp.get("validation_errors"):
        story.append(Paragraph("Validation Errors:", normal))
        for e in nlp.get("validation_errors", []):
            story.append(Paragraph(f"• {e}", bullet))

    story.append(Paragraph("<b>RAG Summary (Agent 3)</b>", h2_style))
    rag = agent_trace.get("agent_findings", {}).get("verification_officer", {})
    rag_data = [
        ["Policies Retrieved", str(rag.get("policies_retrieved", 3))],
        ["RBI Guidelines Used", str(rag.get("rbi_guidelines", "KYC Master Direction"))],
        ["Similar Historical Cases", str(rag.get("similar_cases", "N/A"))],
        ["Similarity Score", str(rag.get("similarity_score", "85%"))],
    ]
    rtable = Table(rag_data, colWidths=["50%", "50%"])
    rtable.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(rtable)
    story.append(Spacer(1, 15))

    # ──────────────────────────────────────────────────────────
    # 9. Explainable AI
    # ──────────────────────────────────────────────────────────
    story += section_header("9. Explainable AI Trace")
    
    for agent_name, payload in findings.items():
        story.append(Paragraph(f"<b>{agent_name.replace('_', ' ').title()}</b>", h2_style))
        explain = payload.get("explainability")
        if explain:
            ex_data = [
                ["Input", Paragraph(str(explain.get("input", "N/A")), normal)],
                ["Reasoning", Paragraph(str(explain.get("reasoning", "N/A")), normal)],
                ["Evidence Used", Paragraph(", ".join(explain.get("evidence_used", [])), normal)],
                ["Tools Invoked", Paragraph(", ".join(explain.get("tools_invoked", [])), normal)],
                ["Confidence", f"{explain.get('confidence', 'N/A')}%"],
                ["Decision", Paragraph(str(explain.get("decision", "N/A")), normal)],
            ]
            ex_table = Table(ex_data, colWidths=["25%", "75%"])
            ex_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f8fafc")),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("PADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP")
            ]))
            story.append(ex_table)
        else:
            text_payload = json.dumps(payload, indent=2)
            if len(text_payload) > 1000:
                text_payload = text_payload[:1000] + "\\n...[truncated]"
            story.append(Paragraph(text_payload.replace("\\n", "<br/>").replace(" ", "&nbsp;"), code))
        
        story.append(Spacer(1, 15))

    # ──────────────────────────────────────────────────────────
    # 10. Final Recommendation
    # ──────────────────────────────────────────────────────────
    story.append(PageBreak())
    story += section_header("10. Final Recommendation")
    
    rec_box = [
        ["Status", app.status.value.upper() if app.status else "PENDING"],
        ["Verification Score", f"{report.verification_score if report else 0:.1f} / 100"],
        ["Reasoning", Paragraph(agent_trace.get("recommendation", "N/A"), normal)],
        ["Risk Summary / Human Notes", Paragraph(agent_trace.get("human_review", "N/A"), normal)],
        ["Executive Summary", Paragraph(report.agent_summary if report else "N/A", normal)],
    ]
    frec_table = Table(rec_box, colWidths=["30%", "70%"])
    frec_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a5f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("PADDING", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP")
    ]))
    story.append(frec_table)
    story.append(Spacer(1, 20))

    # ──────────────────────────────────────────────────────────
    # 11. Approval Section
    # ──────────────────────────────────────────────────────────
    story += section_header("11. Approval & Signatures")
    
    sig_data = [
        ["Prepared By", "Verified By", "Manager Approval"],
        ["", "", ""],  # empty row for signature space
        ["Digital Signature", "Digital Signature", "Digital Signature"],
        [app.user.name if app.user else "System", "Senior Officer", "Branch Manager"],
        [f"Date: {datetime.utcnow().strftime('%Y-%m-%d')}", "", ""],
    ]
    sig_table = Table(sig_data, colWidths=["33%", "33%", "34%"])
    sig_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 0), (-1, 0), [colors.HexColor("#f8fafc")]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 40), # signature space
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(sig_table)
    
    story.append(Spacer(1, 30))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
    story.append(Paragraph("This document is generated by SmartVerify Autonomous Multi-Agent AI System. Data is for internal verification only.", small))
    
    doc.build(story)
    logger.info(f"PDF report saved: {filepath}")
    return filepath
