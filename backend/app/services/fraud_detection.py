"""
Fraud Detection Module
Analyses extracted info and documents for anomalies, duplicates,
and suspicious patterns. Returns a risk score (0–100) and fraud flags.
"""
import re, logging
from typing import Dict, Any, List
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class FraudDetector:

    def analyse(
        self,
        extracted_info: Dict[str, Any],
        documents: List[Dict],
        application_id: int,
        db: Session,
    ) -> Dict[str, Any]:
        """
        Run all fraud checks. Returns:
          - risk_score: 0–100
          - fraud_flag: bool
          - alerts: list of alert strings
          - details: per-check breakdown
        """
        alerts = []
        details = []
        risk_score = 0.0

        # ── 1. Missing critical documents ─────────────────────────────────
        doc_types = {d.get("document_type") for d in documents}
        missing = []
        for req in ("aadhaar", "pan", "salary_slip"):
            if req not in doc_types:
                missing.append(req)
        if missing:
            score_add = 10 * len(missing)
            risk_score += score_add
            msg = f"Missing required documents: {missing}"
            alerts.append(msg)
            details.append({"check": "missing_documents", "risk_added": score_add, "detail": msg})

        # ── 2. Duplicate Aadhaar / PAN check ─────────────────────────────
        from app.models.verification_report import VerificationReport
        aadhaar = extracted_info.get("aadhaar_number")
        pan     = extracted_info.get("pan_number")

        if aadhaar:
            # SQLite compatible check across VerificationReport table
            existing = 0
            reports = db.query(VerificationReport).filter(VerificationReport.application_id != application_id).all()
            for r in reports:
                if r.extracted_info and r.extracted_info.get("aadhaar_number") == aadhaar:
                    existing += 1
            if existing:
                risk_score += 30
                msg = f"Duplicate Aadhaar detected in {existing} other application(s)"
                alerts.append(msg)
                details.append({"check": "duplicate_aadhaar", "risk_added": 30, "detail": msg})

        # ── 3. Unusually high income claim ────────────────────────────────
        income = extracted_info.get("monthly_income")
        loan   = extracted_info.get("loan_amount", 0) or 0
        if income and income > 500000:
            risk_score += 15
            msg = f"Unusually high claimed income: ₹{income}/month"
            alerts.append(msg)
            details.append({"check": "high_income_claim", "risk_added": 15, "detail": msg})

        # ── 4. Loan-to-income ratio ────────────────────────────────────────
        if income and loan:
            lti = loan / (income * 12)
            if lti > 10:
                risk_score += 20
                msg = f"Loan-to-annual-income ratio is very high: {lti:.1f}x"
                alerts.append(msg)
                details.append({"check": "high_lti_ratio", "risk_added": 20, "detail": msg})

        # ── 5. Applicant name inconsistency ──────────────────────────────
        # (In production: compare per-document extracted names)
        if not extracted_info.get("applicant_name"):
            risk_score += 10
            msg = "Applicant name could not be extracted"
            alerts.append(msg)
            details.append({"check": "missing_name", "risk_added": 10, "detail": msg})

        # ── 6. PAN format check ───────────────────────────────────────────
        if pan and not re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", pan):
            risk_score += 25
            msg = f"PAN number format is invalid: {pan}"
            alerts.append(msg)
            details.append({"check": "invalid_pan", "risk_added": 25, "detail": msg})

        risk_score = min(risk_score, 100.0)
        fraud_flag = risk_score >= 70

        return {
            "risk_score": round(risk_score, 2),
            "fraud_flag": fraud_flag,
            "alerts": alerts,
            "details": details,
        }
