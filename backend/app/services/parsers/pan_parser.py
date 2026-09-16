"""
PAN Document Parser & Field Validator
Extracts structured metadata (value, confidence, source, extraction_method, validation_status)
from raw OCR text lines for PAN cards.
"""
import re
from typing import Dict, Any, Optional, List, Tuple

# OCR Character Confusion Mappings
# Positions 0..4 (indices 0..4): Must be letters [A-Z]
# Positions 5..8 (indices 5..8): Must be digits [0-9]
# Position 9 (index 9): Must be letter [A-Z]

CHAR_TO_DIGIT = {
    'O': '0', 'o': '0', 'D': '0', 'Q': '0',
    'I': '1', 'i': '1', 'l': '1', '|': '1', '!': '1', ']': '1', '[': '1',
    'Z': '2', 'z': '2',
    'E': '3',
    'A': '4', 'a': '4',
    'S': '5', 's': '5', '$': '5',
    'G': '6',
    'T': '7', 't': '7',
    'B': '8', 'b': '8',
    'g': '9', 'q': '9',
}

CHAR_TO_LETTER = {
    '0': 'O',
    '1': 'I',
    '2': 'Z',
    '5': 'S',
    '8': 'B',
    '6': 'G',
}

# 4th character entity type in standard Indian PAN:
# P = Individual, C = Company, H = HUF, A = AOP, B = BOI, G = Govt,
# J = Artificial Juridical, L = Local Authority, F = Firm, T = Trust
VALID_ENTITIES = {'P', 'C', 'H', 'A', 'B', 'G', 'J', 'L', 'F', 'T'}

STOP_WORDS = {
    'income', 'tax', 'department', 'govt', 'india', 'permanent', 'account',
    'number', 'card', 'signature', 'date', 'birth', 'father', 'name', 'wait',
    'male', 'female', 'incometax', 'republic', 'pehechan', 'identification',
    'digitally', 'signed', 'holder', 'valid', 'sign'
}


def normalize_pan_token(raw_tok: str) -> Optional[Tuple[str, int]]:
    """
    Attempt to normalize a 10-char candidate token by correcting common OCR confusion characters.
    Returns (normalized_pan, correction_count) if valid format, else None.
    """
    tok = re.sub(r'[^A-Za-z0-9$|!\]\[]', '', raw_tok)
    if len(tok) != 10:
        return None

    chars = list(tok)
    corrections = 0

    # 1. First 5 characters must be alphabetic [A-Z]
    for k in range(5):
        if chars[k].isalpha():
            chars[k] = chars[k].upper()
        elif chars[k] in CHAR_TO_LETTER:
            chars[k] = CHAR_TO_LETTER[chars[k]]
            corrections += 1
        else:
            return None

    # 2. Characters 5..8 (indices 5, 6, 7, 8) must be numeric digits [0-9]
    for k in range(5, 9):
        if chars[k].isdigit():
            pass
        elif chars[k] in CHAR_TO_DIGIT:
            chars[k] = CHAR_TO_DIGIT[chars[k]]
            corrections += 1
        else:
            return None

    # 3. 10th character (index 9) must be alphabetic [A-Z]
    if chars[9].isalpha():
        chars[9] = chars[9].upper()
    elif chars[9] in CHAR_TO_LETTER:
        chars[9] = CHAR_TO_LETTER[chars[9]]
        corrections += 1
    else:
        return None

    # Maximum 3 corrections allowed to prevent false positive matching on random text
    if corrections > 3:
        return None

    cand = ''.join(chars)
    if not re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", cand):
        return None

    return cand, corrections


def extract_pan_candidates(text: str) -> List[Dict[str, Any]]:
    """
    Scan text using multiple strategies:
    1. Direct strict regex match in raw text
    2. Token-by-token normalization with OCR character confusion repair
    3. Collapsed spaced tokens (e.g. 'B R C P Y 1 4 4 0 A')
    4. Proximity scoring based on PAN anchors ('Permanent Account Number', 'Income Tax')
    """
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    candidates: List[Dict[str, Any]] = []
    seen_pans = set()

    # Track anchor line indices
    pan_header_indices = []
    for i, line in enumerate(lines):
        if re.search(r"(?:permanent\s+account|account\s+number|pan\s+card|\bpan\b)", line, re.I):
            pan_header_indices.append(i)

    # Strategy 1: Direct strict regex match
    for m in re.finditer(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b", text.upper()):
        pan_val = m.group(1)
        score = 90
        if pan_val[3] in VALID_ENTITIES:
            score += 10
            if pan_val[3] == 'P':
                score += 5
        candidates.append({
            "pan": pan_val,
            "score": score,
            "confidence": 0.99,
            "corrections": 0,
            "raw_match": m.group(0),
            "method": "strict_regex_verifier"
        })
        seen_pans.add(pan_val)

    # Strategy 2: Line-by-line & collapsed spaced token inspection
    for i, line in enumerate(lines):
        tokens = re.findall(r'[A-Za-z0-9$|!\]\[]+', line)

        # Also consider entire line collapsed
        collapsed_line = re.sub(r'[^A-Za-z0-9$|!\]\[]', '', line)
        all_tokens = list(tokens)
        if len(collapsed_line) >= 10 and collapsed_line not in all_tokens:
            all_tokens.append(collapsed_line)

        # Multi-word combinations (e.g. 'BRCPY 1440A')
        words = line.split()
        if len(words) > 1:
            for w_start in range(len(words)):
                joined = "".join(words[w_start:w_start+4])
                clean_joined = re.sub(r'[^A-Za-z0-9$|!\]\[]', '', joined)
                if len(clean_joined) >= 10 and clean_joined not in all_tokens:
                    all_tokens.append(clean_joined)

        for tok in all_tokens:
            cand_slices = []
            if len(tok) == 10:
                cand_slices.append(tok)
            elif 10 < len(tok) <= 16:
                for off in range(len(tok) - 9):
                    cand_slices.append(tok[off:off+10])

            for slice_tok in cand_slices:
                res = normalize_pan_token(slice_tok)
                if not res:
                    continue

                norm_pan, corrections = res
                if norm_pan in seen_pans:
                    continue

                score = 60 - (corrections * 15)
                if norm_pan[3] in VALID_ENTITIES:
                    score += 15
                    if norm_pan[3] == 'P':
                        score += 5

                proximity_bonus = 0
                for h_idx in pan_header_indices:
                    diff = i - h_idx
                    if diff == 1:
                        proximity_bonus = max(proximity_bonus, 40)
                    elif 1 <= abs(diff) <= 3:
                        proximity_bonus = max(proximity_bonus, 25)

                score += proximity_bonus

                confidence = 0.98 if corrections == 0 else (0.94 if corrections == 1 else 0.88)
                method = "exact_token" if corrections == 0 else "ocr_glitch_corrected"

                candidates.append({
                    "pan": norm_pan,
                    "score": score,
                    "confidence": confidence,
                    "corrections": corrections,
                    "raw_match": slice_tok,
                    "method": method
                })
                seen_pans.add(norm_pan)

    # Strategy 3: Directly examine line immediately following any PAN header
    for h_idx in pan_header_indices:
        if h_idx + 1 < len(lines):
            next_line = lines[h_idx + 1]
            clean_sub = re.sub(r'[^A-Za-z0-9$|!\]\[]', '', next_line)
            if len(clean_sub) >= 10:
                for off in range(len(clean_sub) - 9):
                    slice_tok = clean_sub[off:off+10]
                    res = normalize_pan_token(slice_tok)
                    if res:
                        norm_pan, corrections = res
                        if norm_pan not in seen_pans:
                            score = 85 - (corrections * 15)
                            if norm_pan[3] in VALID_ENTITIES:
                                score += 15
                            candidates.append({
                                "pan": norm_pan,
                                "score": score,
                                "confidence": 0.95 if corrections <= 1 else 0.88,
                                "corrections": corrections,
                                "raw_match": slice_tok,
                                "method": "header_proximity_heuristic"
                            })
                            seen_pans.add(norm_pan)

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates

def parse_pan(text: str) -> Dict[str, Any]:
    """Parse PAN card OCR text and return structured metadata per field."""
    results = {
        "pan_number":     _build_field(None, 0.0, "pan", "none", "not_found"),
        "applicant_name": _build_field(None, 0.0, "pan", "none", "not_found"),
        "father_name":    _build_field(None, 0.0, "pan", "none", "not_found"),
        "dob":            _build_field(None, 0.0, "pan", "none", "not_found"),
    }

    if not text:
        return results

    lines = [l.strip() for l in text.split('\n') if l.strip()]

    # 1. PAN Number Detection
    candidates = extract_pan_candidates(text)
    if candidates:
        best_pan = candidates[0]
        results["pan_number"] = _build_field(
            value=best_pan["pan"],
            confidence=best_pan["confidence"],
            source="pan",
            method=best_pan["method"],
            status="valid",
            evidence=best_pan["raw_match"]
        )
    else:
        results["pan_number"] = _build_field(
            value=None,
            confidence=0.0,
            source="pan",
            method="no_pan_candidate",
            status="low_confidence",
            warning="PAN number could not be extracted confidently. Please upload a clearer image."
        )

    # 2. DOB (Date of Birth) - Glitch-tolerant
    dob = None
    dob_evidence = None
    m = re.search(r"(?:dob|date\s*of\s*birth|yob|birth|जन्म)[:\s,]*([0-9OlIP/\-,\.]{8,15})", text, re.I)
    if m:
        raw_chunk = m.group(1)
        dob_evidence = m.group(0)
        norm = raw_chunk.replace('O', '0').replace('o', '0').replace('I', '1').replace('l', '1').replace('P', '/')
        dm = re.search(r"(\d{2})[/,\-\.]+(\d{2})[/,\-\.]+(\d{4})", norm)
        if dm:
            dob = f"{dm.group(1)}/{dm.group(2)}/{dm.group(3)}"

    if not dob:
        dm2 = re.search(r"\b(\d{2}[/,\-\.]\d{2}[/,\-\.]\d{4})\b", text)
        if dm2:
            dob = dm2.group(1).replace('-', '/').replace('.', '/')
            dob_evidence = dm2.group(0)

    if dob:
        results["dob"] = _build_field(
            value=dob,
            confidence=0.98,
            source="pan",
            method="regex_date_parser",
            status="valid",
            evidence=dob_evidence
        )

    # 3. Applicant Name and Father's Name Extraction
    applicant_name = None
    father_name = None

    # Strategy A: Label-based extraction (Name: ... / Father's Name: ...)
    for i, line in enumerate(lines):
        # Look for Applicant Name label (excluding father/mother lines)
        if not applicant_name and re.search(r"\b(?:name|applicant(?:\'s)?\s*name)\b", line, re.I) and not re.search(r"(?:father|mother|husband|पिता)", line, re.I):
            after = _clean_ocr_name(re.sub(r".*?\bname\b[:\s\-]*", "", line, flags=re.I))
            if _is_valid_name(after):
                applicant_name = after
            elif i + 1 < len(lines):
                cand = _clean_ocr_name(lines[i+1])
                if _is_valid_name(cand):
                    applicant_name = cand

        # Look for Father's Name label
        if not father_name and re.search(r"(?:father(?:'s)?\s*name|पिता)", line, re.I):
            after = _clean_ocr_name(re.sub(r".*?(?:father(?:'s)?\s*name|पिता.*?नाम)[:\s\-]*", "", line, flags=re.I))
            if _is_valid_name(after):
                father_name = after
            elif i + 1 < len(lines):
                cand = _clean_ocr_name(lines[i+1])
                if _is_valid_name(cand):
                    father_name = cand

    # Strategy B: Proximity layout fallback if labels are not found
    if not applicant_name or not father_name:
        header_idx = -1
        for i, line in enumerate(lines):
            if re.search(r"(?:income tax|govt|india|department|आयकर)", line, re.I):
                header_idx = i

        dob_idx = -1
        for i, line in enumerate(lines):
            if re.search(r"\b\d{2}[/\-,\.]\d{2}[/\-,\.]\d{4}\b", line):
                dob_idx = i
                break

        start_k = header_idx + 1 if header_idx >= 0 else 0
        end_k = dob_idx if dob_idx > start_k else len(lines)

        fallback_candidates = []
        for k in range(start_k, end_k):
            cl = _clean_ocr_name(lines[k])
            if _is_valid_name(cl):
                fallback_candidates.append(cl)

        if not applicant_name and len(fallback_candidates) >= 1:
            applicant_name = fallback_candidates[0]

        if not father_name and len(fallback_candidates) >= 2:
            father_name = fallback_candidates[1]

    if applicant_name:
        results["applicant_name"] = _build_field(
            value=applicant_name,
            confidence=0.95,
            source="pan",
            method="pan_name_parser",
            status="valid",
            evidence=applicant_name
        )

    if father_name:
        results["father_name"] = _build_field(
            value=father_name,
            confidence=0.93,
            source="pan",
            method="pan_name_parser",
            status="valid",
            evidence=father_name
        )

    return results


def parse_pan_multipass(passes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Parse PAN card using multi-pass OCR consensus.
    Compares candidate tokens across multiple passes, weighs by OCR confidence,
    applies entity-structure rules, and generates field-level confidence ratings.
    """
    if not passes:
        return parse_pan("")

    if len(passes) == 1:
        return parse_pan(passes[0].get("text", ""))

    pan_cand_map: Dict[str, Dict[str, Any]] = {}
    name_cands: Dict[str, float] = {}
    father_cands: Dict[str, float] = {}
    dob_cands: Dict[str, float] = {}

    for p in passes:
        text = p.get("text", "")
        detections = p.get("detections", [])

        cands = extract_pan_candidates(text)
        for det in detections:
            d_text = det.get("text", "").strip()
            d_conf = det.get("confidence", 0.8)
            norm = normalize_pan_token(d_text)
            if norm:
                cand_pan, corrs = norm
                if cand_pan not in [c["pan"] for c in cands]:
                    cands.append({
                        "pan": cand_pan,
                        "score": 75 - corrs * 15,
                        "confidence": d_conf,
                        "corrections": corrs,
                        "raw_match": d_text,
                        "method": "detection_token"
                    })

        for c in cands:
            pan_val = c["pan"]
            if pan_val not in pan_cand_map:
                pan_cand_map[pan_val] = {
                    "count": 0,
                    "confidences": [],
                    "corrections": c["corrections"],
                    "methods": set(),
                    "raw_matches": [],
                    "scores": []
                }
            pan_cand_map[pan_val]["count"] += 1
            pan_cand_map[pan_val]["confidences"].append(c["confidence"])
            pan_cand_map[pan_val]["methods"].add(c["method"])
            pan_cand_map[pan_val]["raw_matches"].append(c["raw_match"])
            pan_cand_map[pan_val]["scores"].append(c["score"])

        # Name & DOB from this pass
        single_res = parse_pan(text)
        if single_res["applicant_name"]["value"]:
            nm = single_res["applicant_name"]["value"]
            name_cands[nm] = name_cands.get(nm, 0.0) + single_res["applicant_name"]["confidence"]
        if single_res["father_name"]["value"]:
            fn = single_res["father_name"]["value"]
            father_cands[fn] = father_cands.get(fn, 0.0) + single_res["father_name"]["confidence"]
        if single_res["dob"]["value"]:
            db = single_res["dob"]["value"]
            dob_cands[db] = dob_cands.get(db, 0.0) + single_res["dob"]["confidence"]

    results = {
        "pan_number":     _build_field(None, 0.0, "pan", "none", "not_found"),
        "applicant_name": _build_field(None, 0.0, "pan", "none", "not_found"),
        "father_name":    _build_field(None, 0.0, "pan", "none", "not_found"),
        "dob":            _build_field(None, 0.0, "pan", "none", "not_found"),
    }

    # Decide PAN winner
    if pan_cand_map:
        scored_pans = []
        for pan_val, meta in pan_cand_map.items():
            base_conf = sum(meta["confidences"]) / len(meta["confidences"])
            consensus_bonus = 0.15 if meta["count"] >= 2 else 0.0
            entity_bonus = 0.05 if pan_val[3] in VALID_ENTITIES else -0.10
            exact_bonus = 0.05 if meta["corrections"] == 0 else -0.05 * meta["corrections"]

            final_conf = min(0.99, max(0.20, base_conf + consensus_bonus + entity_bonus + exact_bonus))
            composite_score = max(meta["scores"]) + (meta["count"] * 25)
            scored_pans.append((pan_val, final_conf, composite_score, meta))

        scored_pans.sort(key=lambda x: (x[1] >= 0.75, x[2], x[1]), reverse=True)
        best_pan, best_conf, best_score, best_meta = scored_pans[0]

        if best_conf >= 0.75 and re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", best_pan):
            results["pan_number"] = _build_field(
                value=best_pan,
                confidence=best_conf,
                source="pan",
                method=f"multi_pass_consensus_{best_meta['count']}_passes",
                status="valid",
                evidence=", ".join(set(best_meta["raw_matches"][:2]))
            )
        elif best_conf >= 0.60 and re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", best_pan):
            results["pan_number"] = _build_field(
                value=best_pan,
                confidence=best_conf,
                source="pan",
                method="multi_pass_low_confidence",
                status="needs_review",
                evidence=", ".join(set(best_meta["raw_matches"][:2])),
                warning="PAN number confidence is low. Please verify or upload a clearer image."
            )
        else:
            results["pan_number"] = _build_field(
                value=None,
                confidence=best_conf,
                source="pan",
                method="failed_confidence_check",
                status="low_confidence",
                warning="PAN number could not be extracted confidently. Please upload a clearer image."
            )
    else:
        results["pan_number"] = _build_field(
            value=None,
            confidence=0.0,
            source="pan",
            method="no_pan_candidate",
            status="low_confidence",
            warning="PAN number could not be extracted confidently. Please upload a clearer image."
        )

    # Decide Name winner
    if name_cands:
        best_name = sorted(name_cands.items(), key=lambda x: x[1], reverse=True)[0][0]
        name_conf = min(0.98, 0.85 + (0.10 if len(passes) > 1 and name_cands[best_name] > 1.0 else 0.0))
        results["applicant_name"] = _build_field(
            value=best_name,
            confidence=name_conf,
            source="pan",
            method="multi_pass_name_consensus",
            status="valid",
            evidence=best_name
        )

    # Decide Father Name winner
    if father_cands:
        best_father = sorted(father_cands.items(), key=lambda x: x[1], reverse=True)[0][0]
        father_conf = min(0.95, 0.80 + (0.10 if len(passes) > 1 and father_cands[best_father] > 1.0 else 0.0))
        results["father_name"] = _build_field(
            value=best_father,
            confidence=father_conf,
            source="pan",
            method="multi_pass_father_consensus",
            status="valid",
            evidence=best_father
        )

    # Decide DOB winner
    if dob_cands:
        best_dob = sorted(dob_cands.items(), key=lambda x: x[1], reverse=True)[0][0]
        dob_conf = min(0.99, 0.90 + (0.08 if len(passes) > 1 and dob_cands[best_dob] > 1.0 else 0.0))
        results["dob"] = _build_field(
            value=best_dob,
            confidence=dob_conf,
            source="pan",
            method="multi_pass_dob_consensus",
            status="valid",
            evidence=best_dob
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
    cleaned = re.sub(r"^(?:[^\w\s]|[\d_])+", "", cleaned)
    cleaned = re.sub(r"(?i)^(?:applicant\s*)?name[:\s\-\.]*", "", cleaned)
    cleaned = re.sub(r"(?i)^(?:father(?:'s)?\s*)?name[:\s\-\.]*", "", cleaned)
    cleaned = re.sub(r"[^A-Za-z\s\.]", "", cleaned).strip()
    return re.sub(r"\s+", " ", cleaned)


def _is_valid_name(val: str) -> bool:
    if not val or len(val) < 3 or len(val) > 40:
        return False
    words = val.split()
    if any(w.lower() in STOP_WORDS for w in words):
        return False
    if not words[0][0].isupper():
        return False
    if not any(c.lower() in 'aeiou' for c in val):
        return False
    # If it matches PAN pattern, it's not a person's name
    if normalize_pan_token(val):
        return False
    return True
