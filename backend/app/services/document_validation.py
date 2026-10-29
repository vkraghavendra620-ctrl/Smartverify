"""Document validation service for SmartVerify.

Enforces the requirement that ONLY Aadhaar Card and PAN Card are mandatory.
All other document types (employment, property, site images, etc.) remain optional.
"""
from typing import List, Dict, Any, Union


MANDATORY_DOCUMENTS = [
    {"type": "aadhaar", "label": "Aadhaar Card"},
    {"type": "pan", "label": "PAN Card"},
]

MANDATORY_DOCUMENT_TYPES = {"aadhaar", "pan"}


def validate_required_documents(documents: List[Any]) -> Dict[str, Any]:
    """Validate that mandatory documents (Aadhaar Card and PAN Card) are present.

    All other document types are optional and their absence does not cause
    validation to fail.

    Accepts a list of:
      - dicts (e.g. {"document_type": "aadhaar", ...})
      - Document model instances
      - document_type string identifiers

    Returns:
        dict:
            - is_valid (bool): True if both Aadhaar and PAN are present, False otherwise.
            - has_aadhaar (bool): True if Aadhaar is present.
            - has_pan (bool): True if PAN is present.
            - missing_documents (List[str]): Identifiers of missing required docs.
            - missing_labels (List[str]): Clear names of missing required docs.
            - message (str): User-friendly, actionable validation message.
    """
    uploaded_types = set()
    for d in (documents or []):
        if isinstance(d, str):
            uploaded_types.add(d.strip().lower())
        elif isinstance(d, dict):
            # Only consider primary applicant documents if joint index is specified
            if d.get("joint_applicant_index") is not None:
                continue
            dt = d.get("document_type")
            if hasattr(dt, "value"):
                uploaded_types.add(str(dt.value).strip().lower())
            elif dt:
                uploaded_types.add(str(dt).strip().lower())
        else:
            # Model object
            if getattr(d, "joint_applicant_index", None) is not None:
                continue
            dt = getattr(d, "document_type", None)
            if hasattr(dt, "value"):
                uploaded_types.add(str(dt.value).strip().lower())
            elif dt:
                uploaded_types.add(str(dt).strip().lower())

    has_aadhaar = "aadhaar" in uploaded_types
    has_pan = "pan" in uploaded_types

    missing_docs = []
    missing_labels = []

    if not has_aadhaar:
        missing_docs.append("aadhaar")
        missing_labels.append("Aadhaar Card")
    if not has_pan:
        missing_docs.append("pan")
        missing_labels.append("PAN Card")

    if not missing_docs:
        message = "Required documents validation passed: Aadhaar Card and PAN Card are both present."
        is_valid = True
    elif len(missing_docs) == 2:
        message = "Required documents missing: Aadhaar Card and PAN Card are both missing."
        is_valid = False
    elif "aadhaar" in missing_docs:
        message = "Required document missing: Aadhaar Card is missing."
        is_valid = False
    else:
        message = "Required document missing: PAN Card is missing."
        is_valid = False

    return {
        "is_valid": is_valid,
        "has_aadhaar": has_aadhaar,
        "has_pan": has_pan,
        "missing_documents": missing_docs,
        "missing_labels": missing_labels,
        "message": message,
    }
