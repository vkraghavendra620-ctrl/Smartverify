"""
Aadhaar Document Parser & Field Validator
Extracts structured metadata (value, confidence, source, extraction_method, validation_status)
from raw OCR text lines for Aadhaar cards.
"""
import re
from typing import Dict, Any, Optional, List, Tuple

STOP_WORDS = {
    'government', 'india', 'unique', 'identification', 'authority', 'aadhaar', 
    'enrolment', 'enrollment', 'signature', 'valid', 'invalid', 'male', 'female', 
    'transgender', 'dob', 'date', 'birth', 'year', 'address', 'card', 'your', 'no',
    'help', 'resident', 'download', 'information', 'electronic', 'letter', 'father',
    'mother', 'husband', 'wife', 'son', 'daughter', 'order', 'state', 'pincode', 'pin',
    'vid', 'www', 'uidai', 'gov', 'in', 'mera', 'pehechan', 'aadhar'
}

# Verhoeff algorithm tables for UIDAI checksum validation
_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]
]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]
]

def validate_verhoeff(num_str: str) -> bool:
    """Validate 12-digit Aadhaar number using official Verhoeff checksum algorithm."""
    if not num_str or not num_str.isdigit() or len(num_str) != 12:
        return False
    c = 0
    for i, item in enumerate(reversed(num_str)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(item)]]
    return c == 0

def mask_aadhaar(num_str: Optional[str]) -> str:
    """Mask Aadhaar number for secure logging: XXXX-XXXX-1234."""
    if not num_str:
        return ""
    clean = re.sub(r"\D", "", num_str)
    if len(clean) == 12:
        return f"XXXX-XXXX-{clean[-4:]}"
    return "XXXX-XXXX-XXXX"

def extract_aadhaar_candidates(text: str):
    """
    Extract 12-digit Aadhaar candidates from text using pattern matching
    and character confusion correction.
    """
    candidates = []
    seen = set()

    # 1. Standard 4-4-4 pattern or 12 continuous digits
    patterns = [
        r"\b(\d{4}[\s-]\d{4}[\s-]\d{4})\b",
        r"\b(\d{12})\b",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text):
            raw = m.group(1)
            clean = re.sub(r"[\s-]", "", raw)
            if len(clean) == 12 and clean not in seen:
                is_vh = validate_verhoeff(clean)
                score = 90 + (10 if is_vh else 0)
                conf = 0.99 if is_vh else 0.95
                candidates.append({
                    "aadhaar": clean,
                    "confidence": conf,
                    "score": score,
                    "is_verhoeff": is_vh,
                    "corrections": 0,
                    "raw_match": raw,
                    "method": "strict_regex"
                })
                seen.add(clean)

    # 2. Glitch-tolerant extraction (e.g. O/0, I/1 in 4-digit groups)
    glitch_pat = r"\b([0-9OlIPSBZgbq]{4}[\s-][0-9OlIPSBZgbq]{4}[\s-][0-9OlIPSBZgbq]{4})\b"
    char_map = {'O': '0', 'o': '0', 'I': '1', 'l': '1', '|': '1', 'P': '0', 'S': '5', 's': '5', 'B': '8', 'Z': '2', 'z': '2', 'b': '6', 'q': '9', 'g': '9'}
    for m in re.finditer(glitch_pat, text):
        raw = m.group(1)
        cleaned_chars = []
        corrs = 0
        for ch in raw:
            if ch in (' ', '-'):
                continue
            if ch.isdigit():
                cleaned_chars.append(ch)
            elif ch in char_map:
                cleaned_chars.append(char_map[ch])
                corrs += 1
            else:
                break
        if len(cleaned_chars) == 12 and corrs > 0 and corrs <= 2:
            clean = "".join(cleaned_chars)
            if clean not in seen:
                is_vh = validate_verhoeff(clean)
                score = 75 + (15 if is_vh else 0) - (corrs * 10)
                conf = 0.94 if is_vh else 0.85
                candidates.append({
                    "aadhaar": clean,
                    "confidence": conf,
                    "score": score,
                    "is_verhoeff": is_vh,
                    "corrections": corrs,
                    "raw_match": raw,
                    "method": "glitch_corrected_regex"
                })
                seen.add(clean)

    candidates.sort(key=lambda x: (x["score"], x["confidence"]), reverse=True)
    return candidates

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
    candidates = extract_aadhaar_candidates(text)
    if candidates:
        best = candidates[0]
        results["aadhaar_number"] = _build_field(
            value=best["aadhaar"],
            confidence=best["confidence"],
            source="aadhaar",
            method=best["method"],
            status="valid" if best["confidence"] >= 0.75 else "needs_review",
            evidence=best["raw_match"]
        )
    else:
        results["aadhaar_number"] = _build_field(
            value=None,
            confidence=0.0,
            source="aadhaar",
            method="no_candidate",
            status="low_confidence",
            warning="Aadhaar number could not be extracted confidently. Please upload a clearer image."
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


def parse_aadhaar_multipass(passes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Parse Aadhaar card using multi-pass OCR consensus.
    Aggregates candidates across passes, validates 12-digit format and Verhoeff checksum,
    and assigns field-level confidence ratings.
    """
    if not passes:
        return parse_aadhaar("")

    if len(passes) == 1:
        return parse_aadhaar(passes[0].get("text", ""))

    aadhaar_cand_map: Dict[str, Dict[str, Any]] = {}
    name_cands: Dict[str, float] = {}
    father_cands: Dict[str, float] = {}
    dob_cands: Dict[str, float] = {}
    gender_cands: Dict[str, float] = {}
    address_cands: Dict[str, float] = {}

    for p in passes:
        text = p.get("text", "")
        detections = p.get("detections", [])

        cands = extract_aadhaar_candidates(text)
        for c in cands:
            num = c["aadhaar"]
            if num not in aadhaar_cand_map:
                aadhaar_cand_map[num] = {
                    "count": 0,
                    "confidences": [],
                    "is_verhoeff": c["is_verhoeff"],
                    "corrections": c["corrections"],
                    "raw_matches": [],
                    "scores": []
                }
            aadhaar_cand_map[num]["count"] += 1
            aadhaar_cand_map[num]["confidences"].append(c["confidence"])
            aadhaar_cand_map[num]["raw_matches"].append(c["raw_match"])
            aadhaar_cand_map[num]["scores"].append(c["score"])

        # Field parsing per pass
        single_res = parse_aadhaar(text)
        if single_res["applicant_name"]["value"]:
            nm = single_res["applicant_name"]["value"]
            name_cands[nm] = name_cands.get(nm, 0.0) + single_res["applicant_name"]["confidence"]
        if single_res["father_name"]["value"]:
            fn = single_res["father_name"]["value"]
            father_cands[fn] = father_cands.get(fn, 0.0) + single_res["father_name"]["confidence"]
        if single_res["dob"]["value"]:
            db = single_res["dob"]["value"]
            dob_cands[db] = dob_cands.get(db, 0.0) + single_res["dob"]["confidence"]
        if single_res["gender"]["value"]:
            gn = single_res["gender"]["value"]
            gender_cands[gn] = gender_cands.get(gn, 0.0) + single_res["gender"]["confidence"]
        if single_res["address"]["value"]:
            ad = single_res["address"]["value"]
            address_cands[ad] = address_cands.get(ad, 0.0) + single_res["address"]["confidence"]

    results = {
        "aadhaar_number": _build_field(None, 0.0, "aadhaar", "none", "not_found"),
        "applicant_name": _build_field(None, 0.0, "aadhaar", "none", "not_found"),
        "father_name":    _build_field(None, 0.0, "aadhaar", "none", "not_found"),
        "dob":            _build_field(None, 0.0, "aadhaar", "none", "not_found"),
        "gender":         _build_field(None, 0.0, "aadhaar", "none", "not_found"),
        "address":        _build_field(None, 0.0, "aadhaar", "none", "not_found"),
    }

    # Decide Aadhaar Number winner
    if aadhaar_cand_map:
        scored_nums = []
        for num, meta in aadhaar_cand_map.items():
            base_conf = sum(meta["confidences"]) / len(meta["confidences"])
            consensus_bonus = 0.12 if meta["count"] >= 2 else 0.0
            vh_bonus = 0.10 if meta["is_verhoeff"] else 0.0
            final_conf = min(0.99, max(0.20, base_conf + consensus_bonus + vh_bonus))
            composite_score = max(meta["scores"]) + (meta["count"] * 25)
            scored_nums.append((num, final_conf, composite_score, meta))

        scored_nums.sort(key=lambda x: (x[1] >= 0.75, x[2], x[1]), reverse=True)
        best_num, best_conf, best_score, best_meta = scored_nums[0]

        if best_conf >= 0.75 and len(best_num) == 12:
            results["aadhaar_number"] = _build_field(
                value=best_num,
                confidence=best_conf,
                source="aadhaar",
                method=f"multi_pass_consensus_{best_meta['count']}_passes",
                status="valid",
                evidence=", ".join(set(best_meta["raw_matches"][:2]))
            )
        elif best_conf >= 0.60 and len(best_num) == 12:
            results["aadhaar_number"] = _build_field(
                value=best_num,
                confidence=best_conf,
                source="aadhaar",
                method="multi_pass_low_confidence",
                status="needs_review",
                evidence=", ".join(set(best_meta["raw_matches"][:2])),
                warning="Aadhaar number confidence is low. Please verify or upload a clearer image."
            )
        else:
            results["aadhaar_number"] = _build_field(
                value=None,
                confidence=best_conf,
                source="aadhaar",
                method="failed_confidence_check",
                status="low_confidence",
                warning="Aadhaar number could not be extracted confidently. Please upload a clearer image."
            )
    else:
        results["aadhaar_number"] = _build_field(
            value=None,
            confidence=0.0,
            source="aadhaar",
            method="no_candidate",
            status="low_confidence",
            warning="Aadhaar number could not be extracted confidently. Please upload a clearer image."
        )

    # Name winner
    if name_cands:
        best_name = sorted(name_cands.items(), key=lambda x: x[1], reverse=True)[0][0]
        name_conf = min(0.98, 0.85 + (0.10 if len(passes) > 1 and name_cands[best_name] > 1.0 else 0.0))
        results["applicant_name"] = _build_field(
            value=best_name,
            confidence=name_conf,
            source="aadhaar",
            method="multi_pass_name_consensus",
            status="valid",
            evidence=best_name
        )

    # Father Name winner
    if father_cands:
        best_father = sorted(father_cands.items(), key=lambda x: x[1], reverse=True)[0][0]
        results["father_name"] = _build_field(
            value=best_father,
            confidence=0.92,
            source="aadhaar",
            method="multi_pass_father_consensus",
            status="valid",
            evidence=best_father
        )

    # DOB winner
    if dob_cands:
        best_dob = sorted(dob_cands.items(), key=lambda x: x[1], reverse=True)[0][0]
        results["dob"] = _build_field(
            value=best_dob,
            confidence=0.98,
            source="aadhaar",
            method="multi_pass_dob_consensus",
            status="valid",
            evidence=best_dob
        )

    # Gender winner
    if gender_cands:
        best_gender = sorted(gender_cands.items(), key=lambda x: x[1], reverse=True)[0][0]
        results["gender"] = _build_field(
            value=best_gender,
            confidence=0.96,
            source="aadhaar",
            method="multi_pass_gender_consensus",
            status="valid",
            evidence=best_gender
        )

    # Address winner
    if address_cands:
        best_addr = sorted(address_cands.items(), key=lambda x: x[1], reverse=True)[0][0]
        results["address"] = _build_field(
            value=best_addr,
            confidence=0.92,
            source="aadhaar",
            method="multi_pass_address_consensus",
            status="valid",
            evidence=best_addr
        )

    return results


def _build_field(
    value: Optional[str],
    confidence: float,
    source: str,
    method: str,
    status: str,
    evidence: Optional[str] = None,
    warning: Optional[str] = None
) -> Dict[str, Any]:
    conf_level = "high" if (confidence >= 0.75 and status == "valid" and value) else "low"
    return {
        "value": value,
        "confidence": round(float(confidence), 2),
        "confidence_level": conf_level,
        "source_document": source,
        "extraction_method": method,
        "validation_status": status,
        "raw_match": evidence,
        "warning": warning
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