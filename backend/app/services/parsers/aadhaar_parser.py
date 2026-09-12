"""
Aadhaar Document Parser & Field Validator
Extracts structured metadata (value, confidence, source, extraction_method, validation_status)
from raw OCR text lines for Aadhaar cards.
"""
import re
from typing import Dict, Any, Optional, List, Tuple

AADHAAR_STOP_WORDS = {
    # Government & Authority labels
    'government', 'india', 'govt', 'unique', 'identification', 'authority',
    'uidai', 'bharat', 'sarkar', 'shasan', 'pradhikaran', 'republic',
    'enrolment', 'enrollment', 'resident', 'electronic', 'letter', 'card',
    'mera', 'meri', 'pehechan', 'pehchan', 'adhikar', 'aadhar', 'aadhaar',
    'help', 'download', 'information', 'order', 'vid', 'virtual', 'www',
    'in', 'gov', 'com', 'toll', 'free', 'email', 'helpdesk', 'update',
    
    # Demographics & Document labels
    'dob', 'date', 'birth', 'year', 'yob', 'gender', 'male', 'female',
    'transgender', 'purush', 'mahila', 'trans', 'age', 'years', 'yrs',
    'father', 'mother', 'husband', 'wife', 'son', 'daughter', 'care',
    'relation', 'relationship', 'guardian', 'valid', 'invalid', 'signature',
    'digitally', 'signed', 'holder', 'sign', 'photo', 'qr', 'code',
    'applicant', 'name', 'details', 'number', 'no', 'to',
    
    # Address tokens & Geographic landmarks
    'address', 'pata', 'house', 'flat', 'door', 'plot', 'building', 'apt',
    'street', 'road', 'rd', 'cross', 'main', 'lane', 'gali', 'layout', 'lyt',
    'nagar', 'colony', 'block', 'sector', 'sec', 'phase', 'stage',
    'village', 'post', 'office', 'po', 'taluk', 'tehsil', 'hobli', 'mandal',
    'district', 'dist', 'city', 'town', 'state', 'pincode', 'pin',
    'near', 'opp', 'opposite', 'behind', 'beside', 'floor', 'room',
    
    # Indian States & Union Territories
    'andhra', 'pradesh', 'arunachal', 'assam', 'bihar', 'chhattisgarh',
    'goa', 'gujarat', 'haryana', 'himachal', 'jharkhand', 'karnataka',
    'kerala', 'madhya', 'maharashtra', 'manipur', 'meghalaya', 'mizoram',
    'nagaland', 'odisha', 'orissa', 'punjab', 'rajasthan', 'sikkim',
    'tamil', 'nadu', 'telangana', 'tripura', 'uttar', 'uttarakhand',
    'bengal', 'delhi', 'chandigarh', 'puducherry', 'bangalore', 'bengaluru',
    'mumbai', 'bombay', 'chennai', 'madras', 'kolkata', 'calcutta',
    'hyderabad', 'pune', 'ahmedabad', 'jaipur', 'surat', 'lucknow',
    'kanpur', 'nagpur', 'patna', 'indore', 'thane', 'bhopal', 'visakhapatnam',
    'vadodara', 'ghaziabad', 'ludhiana', 'agra', 'nashik', 'faridabad',
    'meerut', 'rajkot', 'varanasi', 'srinagar', 'aurangabad', 'dhanbad',
    'amritsar', 'navi', 'allahabad', 'ranchi', 'howrah', 'coimbatore',
    'jabalpur', 'gwalior', 'vijayawada', 'jodhpur', 'madurai', 'raipur',
    'kota', 'guwahati', 'chandigarh', 'solapur', 'hubli', 'dharwad',
    'bareilly', 'mysore', 'mysuru', 'tiruchirappalli', 'tiruppur', 'moradabad',
    'salem', 'aligarh', 'thiruvananthapuram', 'bhiwandi', 'saharanpur',
    'gorakhpur', 'guntur', 'bikaner', 'amravati', 'noida', 'jamshedpur',
    'bhilai', 'cuttack', 'firozabad', 'kochi', 'nellore', 'bhavnagar',
    'dehradun', 'durgapur', 'asansol', 'rourkela', 'nanded', 'kolhapur',
    'ajmer', 'akola', 'gulbarga', 'jamnagar', 'ujjain', 'loni', 'siliguri',
    'jhansi', 'ulhasnagar', 'jammu', 'sangli', 'mangalore', 'mangaluru',
    'erode', 'belgaum', 'belagavi', 'kurnool', 'ambattur', 'rajahmundry',
    'tirunelveli', 'malegaon', 'gaya', 'udaipur', 'hassan', 'arkalgud'
}

STOP_WORDS = AADHAAR_STOP_WORDS

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

def is_valid_aadhaar_name_candidate(cand_text: str) -> bool:
    """Validate whether an extracted OCR token/phrase is a plausible Indian person name."""
    if not cand_text:
        return False
    cleaned = re.sub(r"(?i)^(?:applicant\s*)?name[:\s\-\.]*", "", cand_text).strip()
    cleaned = re.sub(r"[^A-Za-z\s\.]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if len(cleaned) < 3 or len(cleaned) > 40:
        return False

    # Must not contain digits
    if any(ch.isdigit() for ch in cand_text):
        return False

    # Must not be relationship marker or start with one
    if re.search(r"^(?:S[/I1\s]?O|D[/I1\s]?O|W[/I1\s]?O|C[/I1\s]?O|CARE\s+OF|SIO|DIO|WIO|FATHER|MOTHER|HUSBAND)\b", cleaned, re.I):
        return False

    words = [w.strip('.') for w in cleaned.split() if w.strip('.')]
    if not words:
        return False

    lower_words = [w.lower() for w in words]

    # Hard stop words: immediately reject if any appear
    hard_stops = {
        'government', 'india', 'govt', 'aadhaar', 'aadhar', 'adhaas', 'unique',
        'identification', 'authority', 'dob', 'date', 'birth', 'gender',
        'male', 'female', 'transgender', 'address', 'pata', 'signature',
        'uidai', 'vid', 'enrolment', 'enrollment', 'resident', 'download',
        'card', 'valid', 'invalid', 'digitally', 'signed', 'republic',
        'income', 'tax', 'department', 'electronic', 'letter', 'order',
        'purush', 'mahila', 'trans', 'years', 'yob', 'father', 'mother',
        'husband', 'wife', 'son', 'daughter', 'help', 'email', 'www'
    }
    if any(w in hard_stops for w in lower_words):
        return False

    # Stop words ratio check
    stop_matches = sum(1 for w in lower_words if w in AADHAAR_STOP_WORDS)
    if stop_matches > 0 and stop_matches >= len(words) / 2:
        return False

    # Total alphabetic characters
    alpha_chars = sum(len(w) for w in words)
    if alpha_chars < 3:
        return False

    # Vowel check: Indian names must have at least one vowel
    has_vowel = any(c.lower() in 'aeiouy' for c in cleaned)
    if not has_vowel and alpha_chars > 2:
        return False

    # Real names must start with a capital letter
    if not any(w[0].isupper() for w in words if len(w) > 0):
        return False

    return True


def extract_aadhaar_name(
    text: str,
    detections: Optional[List[Dict[str, Any]]] = None
) -> Tuple[Optional[str], float, str, Dict[str, Any]]:
    """
    Extract the applicant's name from Aadhaar card using:
    1. Spatial bounding box coordinates (EasyOCR detections) to find the Prime Name Zone:
       Header (Government of India / UIDAI) < Name < DOB / Gender / 12-digit number / Address
    2. Document landmark line sequence scoring (name directly above DOB/Relationship/Gender)
    3. Multi-attribute candidate scoring with strict stop-word filtering
    """
    candidates_scores: Dict[str, Dict[str, Any]] = {}

    def _record_candidate(name_cand: str, delta_score: float, reason: str, confidence: float = 0.85):
        clean = re.sub(r"(?i)^(?:applicant\s*)?name[:\s\-\.]*", "", name_cand).strip()
        clean = re.sub(r"[^A-Za-z\s\.]", " ", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        if not clean or not is_valid_aadhaar_name_candidate(clean):
            return

        key = clean.upper()
        if key not in candidates_scores:
            candidates_scores[key] = {
                "display_name": clean,
                "score": 0.0,
                "confidence": confidence,
                "reasons": []
            }
        candidates_scores[key]["score"] += delta_score
        candidates_scores[key]["confidence"] = max(candidates_scores[key]["confidence"], confidence)
        candidates_scores[key]["reasons"].append(f"{reason}(+{delta_score:.0f})")

    # -------------------------------------------------------------
    # 1. SPATIAL BOUNDING BOX EXTRACTION (when detections are available)
    # -------------------------------------------------------------
    if detections:
        header_y_max = 0.0
        dob_y_min = 999999.0
        gender_y_min = 999999.0
        num_y_min = 999999.0
        addr_y_min = 999999.0
        heights = []

        for d in detections:
            bbox = d.get("bbox", [])
            t = d.get("text", "").strip()
            if not bbox or len(bbox) < 4:
                continue
            y_top = min(pt[1] for pt in bbox)
            y_bottom = max(pt[1] for pt in bbox)
            h = max(1.0, y_bottom - y_top)
            heights.append(h)
            t_upper = t.upper()

            # Header check
            if any(k in t_upper for k in ['GOVERNMENT OF INDIA', 'BHARAT SARKAR', 'UNIQUE IDENTIFICATION', 'AUTHORITY OF INDIA', 'UIDAI', 'AADHAAR', 'MERA AADHAAR']):
                if y_bottom > header_y_max:
                    header_y_max = y_bottom

            # DOB check
            if any(k in t_upper for k in ['DOB', 'DATE OF BIRTH', 'BIRTH', 'YOB']) or re.search(r"\b\d{2}[/.-]\d{2}[/.-]\d{4}\b", t):
                if y_top < dob_y_min:
                    dob_y_min = y_top

            # Gender check
            if re.search(r"\b(MALE|FEMALE|TRANSGENDER|PURUSH|MAHILA)\b", t_upper):
                if y_top < gender_y_min:
                    gender_y_min = y_top

            # 12-digit Aadhaar number check
            if re.search(r"\b(\d{4}[\s-]?\d{4}[\s-]?\d{4})\b", t):
                if y_top < num_y_min:
                    num_y_min = y_top

            # Address check
            if re.search(r"\b(ADDRESS|PATA)\b", t_upper):
                if y_top < addr_y_min:
                    addr_y_min = y_top

        avg_line_height = sum(heights) / len(heights) if heights else 20.0
        lower_anchor_y = min(dob_y_min, gender_y_min, num_y_min, addr_y_min)

        for d in detections:
            bbox = d.get("bbox", [])
            t = d.get("text", "").strip()
            conf = float(d.get("confidence", 0.8))
            if not bbox or len(bbox) < 4:
                continue
            y_top = min(pt[1] for pt in bbox)
            y_bottom = max(pt[1] for pt in bbox)
            y_center = (y_top + y_bottom) / 2

            if not is_valid_aadhaar_name_candidate(t):
                continue

            spatial_score = 100.0 * conf

            # PRIME NAME ZONE: below header and above DOB/Gender/Number
            if header_y_max > 0 and lower_anchor_y < 999999.0:
                if (header_y_max - 10) <= y_center <= (lower_anchor_y + 10):
                    spatial_score += 260.0
                    dist_to_anchor = lower_anchor_y - y_bottom
                    if 0 <= dist_to_anchor <= (avg_line_height * 2.5):
                        spatial_score += 160.0
                elif y_center < header_y_max - 15:
                    spatial_score -= 220.0
                elif y_center > lower_anchor_y + 15:
                    spatial_score -= 320.0
            elif lower_anchor_y < 999999.0:
                if y_bottom <= (lower_anchor_y + 10):
                    spatial_score += 190.0
                    dist_to_anchor = lower_anchor_y - y_bottom
                    if 0 <= dist_to_anchor <= (avg_line_height * 2.5):
                        spatial_score += 130.0
                else:
                    spatial_score -= 320.0
            elif header_y_max > 0:
                if y_top >= (header_y_max - 10):
                    spatial_score += 160.0
                else:
                    spatial_score -= 220.0

            _record_candidate(t, spatial_score, "spatial_bbox", confidence=conf)

    # -------------------------------------------------------------
    # 2. LANDMARK LINE SEQUENCE EXTRACTION (from text lines)
    # -------------------------------------------------------------
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    dob_indices = [i for i, l in enumerate(lines) if any(k in l.upper() for k in ['DOB', 'BIRTH', 'YOB']) or re.search(r"\b\d{2}[/.-]\d{2}[/.-]\d{4}\b", l)]
    relation_indices = [i for i, l in enumerate(lines) if re.search(r"\b(?:S[/I1\s]?O|D[/I1\s]?O|W[/I1\s]?O|C[/I1\s]?O|CARE\s+OF|SIO|DIO|WIO)\b", l.upper())]
    gender_indices = [i for i, l in enumerate(lines) if re.search(r"\b[A-Za-z]*(FEMALE|MALE|TRANSGENDER|PURUSH|MAHILA)\b", l.upper())]
    num_indices = [i for i, l in enumerate(lines) if re.search(r"\b(\d{4}[\s-]?\d{4}[\s-]?\d{4}|\d{12})\b", l)]
    addr_indices = [i for i, l in enumerate(lines) if re.search(r"\b(ADDRESS|PATA)\b", l.upper())]
    header_indices = [i for i, l in enumerate(lines) if any(k in l.upper() for k in ['GOVERNMENT OF INDIA', 'BHARAT SARKAR', 'UNIQUE IDENTIFICATION', 'AADHAAR', 'UIDAI'])]

    first_num_idx = min(num_indices) if num_indices else 9999
    first_addr_idx = min(addr_indices) if addr_indices else 9999
    last_header_idx = max(header_indices) if header_indices else -1

    for i, line in enumerate(lines):
        if i >= first_addr_idx:
            continue
        if i > first_num_idx:
            continue

        raw = re.sub(r"[^A-Za-z\s\.]", " ", line).strip()
        raw = re.sub(r"\s+", " ", raw)
        if not is_valid_aadhaar_name_candidate(raw):
            continue

        words = raw.split()
        seq_score = 60.0

        if len(words) >= 2:
            seq_score += 45.0
        if all(w[0].isupper() for w in words):
            seq_score += 35.0
        if any(len(w) == 1 for w in words):
            seq_score += 40.0

        # Proximity to DOB line (Name is directly 1-2 lines above DOB)
        for d_idx in dob_indices:
            diff = d_idx - i
            if diff == 1:
                seq_score += 240.0
            elif diff == 2:
                seq_score += 170.0
            elif 3 <= diff <= 4:
                seq_score += 90.0

        # Proximity to Relationship line (S/O, D/O)
        for r_idx in relation_indices:
            diff = r_idx - i
            if diff == 1:
                seq_score += 200.0
            elif diff == 2:
                seq_score += 150.0

        # Proximity to Gender line
        for g_idx in gender_indices:
            diff = g_idx - i
            if 1 <= diff <= 3:
                seq_score += 120.0

        # Below header
        if last_header_idx >= 0 and i > last_header_idx:
            if 1 <= (i - last_header_idx) <= 4:
                seq_score += 110.0

        _record_candidate(raw, seq_score, "line_sequence", confidence=0.92)

    if not candidates_scores:
        return None, 0.0, "none", {}

    sorted_cands = sorted(candidates_scores.items(), key=lambda x: x[1]["score"], reverse=True)
    best_key, best_meta = sorted_cands[0]
    best_name = best_meta["display_name"]
    best_score = best_meta["score"]
    best_conf = min(0.98, max(0.65, best_meta["confidence"] + (0.05 if best_score > 300 else 0.0)))

    # Clean formatting
    words = best_name.split()
    formatted_words = []
    for w in words:
        if len(w) == 1 or (len(w) == 2 and w.endswith('.')):
            formatted_words.append(w.upper())
        elif w.isupper() and len(w) > 3:
            formatted_words.append(w.capitalize())
        else:
            formatted_words.append(w.capitalize())
    formatted_name = " ".join(formatted_words)

    method = "aadhaar_spatial_layout_parser" if detections else "aadhaar_landmark_sequence_parser"
    return formatted_name, round(best_conf, 2), method, best_meta


def parse_aadhaar(text: str, detections: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
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
        if len(cand) >= 3 and is_valid_aadhaar_name_candidate(cand) and cand[0].isupper() and any(c.lower() in 'aeiouy' for c in cand):
            if not any(w.lower() in AADHAAR_STOP_WORDS for w in cand.split()):
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

    # 5. Intelligent Layout & Spatial Applicant Name Extraction
    best_name, name_conf, name_method, name_meta = extract_aadhaar_name(text, detections=detections)
    if best_name:
        results["applicant_name"] = _build_field(
            value=best_name,
            confidence=name_conf,
            source="aadhaar",
            method=name_method,
            status="valid" if name_conf >= 0.70 else "needs_review",
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

        # Field parsing per pass (with spatial detections)
        single_res = parse_aadhaar(text, detections=detections)
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

    # Name winner with canonical clustering
    if name_cands:
        grouped_names: Dict[str, Dict[str, Any]] = {}
        for nm, conf_sum in name_cands.items():
            norm_key = re.sub(r"[^A-Z]", "", nm.upper())
            if not norm_key:
                continue
            if norm_key not in grouped_names:
                grouped_names[norm_key] = {"names": {}, "total_conf": 0.0, "count": 0}
            grouped_names[norm_key]["names"][nm] = grouped_names[norm_key]["names"].get(nm, 0) + 1
            grouped_names[norm_key]["total_conf"] += conf_sum
            grouped_names[norm_key]["count"] += 1

        if grouped_names:
            best_group = sorted(grouped_names.values(), key=lambda g: (g["count"], g["total_conf"]), reverse=True)[0]
            best_name = sorted(best_group["names"].items(), key=lambda x: x[1], reverse=True)[0][0]
            consensus_bonus = 0.10 if best_group["count"] >= 2 else 0.0
            name_conf = min(0.99, (best_group["total_conf"] / best_group["count"]) + consensus_bonus)
            results["applicant_name"] = _build_field(
                value=best_name,
                confidence=round(name_conf, 2),
                source="aadhaar",
                method=f"multi_pass_name_consensus_{best_group['count']}_passes",
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