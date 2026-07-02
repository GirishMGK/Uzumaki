"""
Regex patterns for Indian-specific sensitive data detection.
Each group maps to a user-facing toggle in the Rules tab.
"""

PATTERN_GROUPS = {
    "PAN_TAN": {
        "label": "PAN & TAN Numbers",
        "patterns": [
            (r'\b[A-Z]{5}[0-9]{4}[A-Z]\b', "PAN Number"),
            (r'\b[A-Z]{4}[0-9]{5}[A-Z]\b', "TAN Number"),
        ],
    },
    "GST_CIN": {
        "label": "GST & CIN Numbers",
        "patterns": [
            (r'\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]\b', "GSTIN"),
            (r'\b[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b', "CIN"),
        ],
    },
    "AADHAAR_PHONE": {
        "label": "Aadhaar & Phone Numbers",
        "patterns": [
            # Aadhaar: 12 digits in groups of 4, first digit 2-9
            (r'\b[2-9]\d{3}[\s\-]?\d{4}[\s\-]?\d{4}\b', "Aadhaar Number"),
            # Indian mobile: optional +91 prefix, starts with 6-9, 10 digits
            (r'\b(?:\+91[\s\-]?)?[6-9]\d{9}\b', "Phone Number"),
        ],
    },
    "EMAIL": {
        "label": "Email Addresses",
        "patterns": [
            (r'\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b', "Email Address"),
        ],
    },
}


def get_active_patterns(enabled_groups: dict) -> list:
    """
    Returns a flat list of (regex_string, label) tuples for enabled groups.
    enabled_groups: {group_key: bool}
    """
    result = []
    for key, enabled in enabled_groups.items():
        if enabled and key in PATTERN_GROUPS:
            result.extend(PATTERN_GROUPS[key]["patterns"])
    return result
