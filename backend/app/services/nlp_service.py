"""
NLP Information Extraction Service
Extracts structured fields from raw OCR text using spaCy and regex patterns.
"""
import re, logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# Lazy-load spaCy model
_nlp = None

def _get_nlp():
    global _nlp
    if _nlp is None:
        try:
            import spacy
            from app.core.config import settings
            _nlp = spacy.load(settings.SPACY_MODEL)
            logger.info("spaCy model loaded")
        except Exception as e:
            logger.warning(f"spaCy unavailable: {e}")
    return _nlp


def extract_information(text: str, doc_type: str = "generic") -> Dict[str, Any]:
    """
    Extract key fields from OCR text.
    Returns a dict with all extracted fields.
    """
    info: Dict[str, Any] = {
        "applicant_name": None,
        "address": None,
        "aadhaar_number": None,
        "pan_number": None,
        "employer_name": None,
        "monthly_income": None,
        "bank_account": None,
        "loan_amount": None,
        "dob": None,
        "phone": None,
        "gender": None,
        "document_type": doc_type,
    }

    if not text:
        return info

    # ── Aadhaar Number (12 digits, may be spaced) ─────────────────────────
    aadhaar_match = re.search(r"\b(\d{4}[\s-]?\d{4}[\s-]?\d{4})\b", text)
    if aadhaar_match:
        info["aadhaar_number"] = re.sub(r"[\s-]", "", aadhaar_match.group())

    # ── PAN Number (ABCDE1234F pattern) ───────────────────────────────────
    pan_match = re.search(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b", text)
    if pan_match:
        info["pan_number"] = pan_match.group()

    # ── Phone number ─────────────────────────────────────────────────────
    phone_match = re.search(r"\b(?:\+91[\s-]?)?[6-9]\d{9}\b", text)
    if phone_match:
        info["phone"] = phone_match.group()

    # ── Income / Salary ──────────────────────────────────────────────────
    income_patterns = [
        r"(?:net salary|gross salary|monthly income|total salary)[:\s]+(?:INR|Rs\.?|₹)?\s*([\d,]+(?:\.\d{1,2})?)",
        r"(?:INR|Rs\.?|₹)\s*([\d,]+(?:\.\d{1,2})?)",
    ]
    for pat in income_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                info["monthly_income"] = float(m.group(1).replace(",", ""))
                break
            except ValueError:
                pass

    # ── Loan amount ───────────────────────────────────────────────────────
    loan_match = re.search(
        r"(?:loan amount|amount requested)[:\s]+(?:INR|Rs\.?|₹)?\s*([\d,]+(?:\.\d{1,2})?)",
        text, re.IGNORECASE
    )
    if loan_match:
        try:
            info["loan_amount"] = float(loan_match.group(1).replace(",", ""))
        except ValueError:
            pass

    # ── Bank account ──────────────────────────────────────────────────────
    acct_match = re.search(r"(?:account no|account number|a/c no)[.:\s]+([\dX*]+)", text, re.IGNORECASE)
    if acct_match:
        info["bank_account"] = acct_match.group(1)

    # ── NER via spaCy ─────────────────────────────────────────────────────
    nlp = _get_nlp()
    if nlp:
        doc = nlp(text[:5000])  # limit to first 5000 chars for speed
        persons = [ent.text for ent in doc.ents if ent.label_ == "PERSON"]
        orgs    = [ent.text for ent in doc.ents if ent.label_ == "ORG"]
        if persons:
            info["applicant_name"] = persons[0]
        if orgs and not info["employer_name"]:
            info["employer_name"] = orgs[0]

    # ── DOB (Date of Birth) ───────────────────────────────────────────────
    dob_match = re.search(r"\b(\d{2}[/\-]\d{2}[/\-]\d{4})\b", text)
    if dob_match:
        info["dob"] = dob_match.group(1)

    # ── Gender ────────────────────────────────────────────────────────────
    gender_match = re.search(r"\b(Male|Female|Transgender)\b", text, re.IGNORECASE)
    if gender_match:
        info["gender"] = gender_match.group(1).title()

    # ── Address: grab lines containing PIN code ───────────────────────────
    addr_match = re.search(r"([^\n]{20,100}\b\d{6}\b)", text)
    if addr_match:
        info["address"] = addr_match.group(1).strip()

    return info
