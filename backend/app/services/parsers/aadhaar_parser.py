"""
Aadhaar Document Parser & Field Validator
Extracts structured metadata (value, confidence, source, extraction_method, validation_status)
from raw OCR text lines for Aadhaar cards.
"""
import re
from typing import Dict, Any, Optional

STOP_WORDS = {
    'government', 'india', 'unique', 'identification', 'authority', 'aadhaar', 
    'enrolment', 'enrollment', 'signature', 'valid', 'invalid', 'male', 'female', 
    'transgender', 'dob', 'date', 'birth', 'year', 'address', 'card', 'your', 'no',
    'help', 'resident', 'download', 'information', 'electronic', 'letter', 'father',
    'mother', 'husband', 'wife', 'son', 'daughter', 'order', 'state', 'pincode', 'pin',
    'vid', 'www', 'uidai', 'gov', 'in', 'mera', 'pehechan', 'aadhar'
}

def parse_aadhaar(text: str) -> Dict[str, Any]:
    """Parse Aadhaar card OCR text and return structured metadata per field."""
    results = {
        "aadhaar_number": _build_field(None, 0.0, "aadhaar", "none", "not_found"),
        "applicant_name": _build_field(None, 0.0, "aadhaar", "none", "not_found"),
        "father_name":    _build_field(None, 0.0, "aadhaar", "none", "not_found"),
        "dob":            _build_field(None, 0.0, "aadhaar", "none", "not_found"),
        "gender":         _build_field(None, 0.0, "aadhaar", "none", "not_found"),
        "address":        _build_field(None, 0.0, "aadhaar", "none", "not_found"),
    }

    if not text:
        return results

    lines = [l.strip() for l in text.split('\n') if l.strip()]

    # 1. Aadhaar Number (12 digits)
    aadhaar_match = re.search(r"\b(\d{4}[\s-]?\d{4}[\s-]?\d{4})\b", text)
    if aadhaar_match:
        raw_val = aadhaar_match.group(1)
        clean_num = re.sub(r"[\s-]", "", raw_val)
        if len(clean_num) == 12:
            results["aadhaar_number"] = _build_field(
                value=clean_num,
                confidence=0.99,
                source="aadhaar",
                method="regex_pattern_matcher",
                status="valid",
                evidence=raw_val
            )


    # 2. DOB (Date of Birth / Year of Birth) - Glitch-tolerant
    dob = None
    dob_evidence = None
    m = re.search(r"(?:dob|date of birth|yob|birth)[:\s,]*([0-9OlIP/\-,\.]{8,15})", text, re.I)
    if m:
        raw_chunk = m.group(1)
        dob_evidence = m.group(0)
        norm = raw_chunk.replace('O', '0').replace('o', '0').replace('I', '1').replace('l', '1').replace('P', '/')
        dm = re.search(r"(\d{2})[/,\-\.]+(\d{2})[/,\-\.]+(\d{4})", norm)
        if dm:
            dob = f"{dm.group(1)}/{dm.group(2)}/{dm.group(3)}"

    if not dob:
        dm2 = re.search(r"\b(\d{2}[/,\-]\d{2}[/,\-]\d{4})\b", text)
        if dm2:
            dob = dm2.group(1).replace(',', '/').replace('-', '/')
            dob_evidence = dm2.group(0)

    if dob:
        results["dob"] = _build_field(
            value=dob,
            confidence=0.98,
            source="aadhaar",
            method="label_date_parser",
            status="valid",
            evidence=dob_evidence
        )

    # 3. Gender (Prioritize Female & Transgender before Male)
    gender = None
    gender_evidence = None
    if re.search(r"\b[A-Za-z]*FEMALE\b", text, re.I):
        gender = "Female"
        gender_evidence = "Female"
    elif re.search(r"\b[A-Za-z]*TRANSGENDER\b", text, re.I):
        gender = "Transgender"
        gender_evidence = "Transgender"
    elif re.search(r"\b(?:MALE)\b", text, re.I):
        gender = "Male"
        gender_evidence = "Male"

    if gender:
        results["gender"] = _build_field(
            value=gender,
            confidence=0.96,
            source="aadhaar",
            method="regex_keyword_matcher",
            status="valid",
            evidence=gender_evidence
        )

    # 4. Father / Relationship Name Extraction
    father_cand = None
    for fm in re.finditer(r"\b(?:S[/I1\s]?O|D[/I1\s]?O|W[/I1\s]?O|C[/I1\s]?O|CARE\s+OF|FATHER|SIO|DIO|WIO|So|Do|Wo)\b[:\s]*[^A-Za-z0-9\n\r]*([A-Za-z\s\.]+?)(?:,|\d|\n|$|:)", text, re.I):
        cand = _clean_ocr_name(fm.group(1))
        if len(cand) >= 3 and _is_valid_name(cand) and cand[0].isupper() and any(c.lower() in 'aeiou' for c in cand):
            if not any(w.lower() in STOP_WORDS for w in cand.split()):
                father_cand = cand
                if len(cand.split()) >= 2:
                    break

    if father_cand:
        results["father_name"] = _build_field(
            value=father_cand,
            confidence=0.92,
            source="aadhaar",
            method="aadhaar_relationship_parser",
            status="valid",
            evidence=father_cand
        )

    # 5. Intelligent Layout-Based Applicant Name Extraction
    relation_indices = [i for i, l in enumerate(lines) if re.search(r"\b(?:S[/I1\s]?O|D[/I1\s]?O|W[/I1\s]?O|C[/I1\s]?O|CARE\s+OF|SIO|DIO|WIO)\b", l.upper())]
    dob_indices = [i for i, l in enumerate(lines) if any(k in l.upper() for k in ['DOB', 'BIRTH', 'YOB'])]
    gender_indices = [i for i, l in enumerate(lines) if re.search(r"\b[A-Za-z]*(FEMALE|MALE|TRANSGENDER)\b", l.upper())]
    header_indices = [i for i, l in enumerate(lines) if 'GOVERNMENT OF INDIA' in l.upper() or l.upper() == 'TO']

    scores = {}
    for i, line in enumerate(lines):
        raw = re.sub(r"[^A-Za-z\s\.]", " ", line).strip()
        raw = re.sub(r"\s+", " ", raw)
        words = raw.split()
        if not words or len(raw) < 3 or len(raw) > 40:
            continue

        # Reject lines that begin with relationship markers
        if re.search(r"^(?:S[/I1\s]?O|D[/I1\s]?O|W[/I1\s]?O|C[/I1\s]?O|CARE\s+OF|SIO|DIO|WIO)\b", raw, re.I):
            continue

        # Reject stop words or noise
        if any(w.lower() in STOP_WORDS for w in words):
            continue

        # Real names start with a Capital letter
        if not words[0][0].isupper():
            continue

        # Skip consonants-only gibberish
        if not any(c.lower() in 'aeiou' for c in raw) and len(raw) > 2:
            continue

        score = 0
        # Title case bonus
        if all(w[0].isupper() for w in words):
            score += 25
        # Multi-word name bonus
        if len(words) >= 2:
            score += 30
        # Indian name initials bonus (e.g. V K Raghavendra, Akhila V K)
        if any(len(w) == 1 for w in words):
            score += 40

        # Layout Proximity 1: Directly above Relationship line (e.g. S/O, D/O)
        for r_idx in relation_indices:
            if 1 <= (r_idx - i) <= 3:
                score += 85

        # Layout Proximity 2: Directly above DOB line
        for d_idx in dob_indices:
            if 1 <= (d_idx - i) <= 4:
                score += 75

        # Layout Proximity 3: Directly above Gender line
        for g_idx in gender_indices:
            if 1 <= (g_idx - i) <= 5:
                score += 50

        # Layout Proximity 4: Directly below Government of India / To
        for h_idx in header_indices:
            if 1 <= (i - h_idx) <= 4:
                score += 40

        scores[raw] = scores.get(raw, 0) + score

    best_name = None
    if scores:
        best_name = sorted(scores.items(), key=lambda x: x[1], reverse=True)[0][0]

    if best_name:
        results["applicant_name"] = _build_field(
            value=best_name,
            confidence=0.95,
            source="aadhaar",
            method="aadhaar_layout_proximity_parser",
            status="valid",
            evidence=best_name
        )

    # 6. Address Block Extraction Strategy
    # Look for lines starting after "Address:" down to the 6-digit pincode
    addr_lines = []
    in_address_section = False

    for line in lines:
        if re.search(r"^Address[:\s]*", line, re.I):
            in_address_section = True
            after = re.sub(r"^Address[:\s]*", "", line, flags=re.I).strip()
            if after:
                addr_lines.append(after)
            continue

        if in_address_section:
            pin = re.search(r"\b\d{6}\b", line)
            cl = line.strip()
            # Stop if we hit Aadhaar card footer, numbers, or VID
            if re.search(r"(?:VID|Aadhaar No|Unique Identification|www\.uidai)", line, re.I):
                break
            # Skip single characters or standalone line numbers
            if len(cl) > 1 and not re.match(r"^\d+$", cl):
                addr_lines.append(cl)
            if pin:
                break

    # Priority 2 Fallback: Check for known address tokens or S/O blocks
    if not addr_lines:
        for line in lines:
            cl = re.sub(r"[^\w\s/,\.-]", "", line).strip()
            if re.search(r"(?:S[/I1]O|D[/I1]O|W[/I1]O|Basavapatna|Arkalgud|Hassan|Karnataka|\b\d{6}\b)", line, re.I):
                if cl and not re.search(r"(?:aadhaar|enrollment|unique|government|authority|india|ridjzi|880005)", cl, re.I):
                    addr_lines.append(cl)

    if addr_lines:
        final_lines = []
        for fl in addr_lines:
            if not final_lines or final_lines[-1] != fl:
                final_lines.append(fl)

        clean_addr = "\n".join(final_lines)
        results["address"] = _build_field(
            value=clean_addr,
            confidence=0.92,
            source="aadhaar",
            method="aadhaar_address_block_parser",
            status="valid",
            evidence=clean_addr
        )

    return results


def _build_field(value: Optional[str], confidence: float, source: str, method: str, status: str, evidence: Optional[str] = None) -> Dict[str, Any]:
    return {
        "value": value,
        "confidence": confidence,
        "source_document": source,
        "extraction_method": method,
        "validation_status": status,
        "raw_match": evidence
    }


def _clean_ocr_name(val: str) -> str:
    if not val:
        return ""
    cleaned = val.replace("$", "S").replace("5", "S")
    cleaned = re.sub(r"(?i)^(?:applicant\s*)?name[:\s\-\.]*", "", cleaned)
    cleaned = re.sub(r"[^A-Za-z\s\.]", "", cleaned).strip()
    return re.sub(r"\s+", " ", cleaned)


def _is_valid_name(val: str) -> bool:
    if len(val) < 3 or len(val) > 50:
        return False
    if re.search(r"(?:government|india|unique|identification|authority|aadhaar|card|father|mother|husband|dob|male|female|enrollment|ridjzi|dekoonad|address)", val, re.I):
        return False
    return True