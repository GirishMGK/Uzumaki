"""Firm-facing labels for the staff/clients bulk-import columns.

These are the single source of truth for three things at once: what the
Masters page's Add-one form dropdowns show, what a downloadable Excel
template's dropdown validation offers, and what the bulk importer accepts
in a filled-in file. Keeping all three driven from the same dict means a
spreadsheet built from the in-app template always validates cleanly, and
there is exactly one place to add a new option if the firm's vocabulary
changes (e.g. a new office city, a new partner).

Each *_MAP dict is {label the admin sees/types: backend value the API
takes}. `.keys()` gives the dropdown/allowed-value list; label lookup is
case-insensitive since a hand-typed Excel cell may not match casing
exactly, but the *_MAP key casing is still what's shown in a generated
dropdown.
"""

DESIGNATION_MAP: dict[str, tuple[str, str, int]] = {
    # label -> (designation, staff_category, grade_rank)
    "Partner": ("PARTNER", "PARTNER", 2),
    "Senior Manager": ("SENIOR_MANAGER", "EMPLOYEE_CA", 4),
    "Manager": ("MANAGER", "EMPLOYEE_CA", 5),
    "Executive": ("EXECUTIVE", "EMPLOYEE_OTHER_PROF", 9),
    "Article": ("ARTICLE_Y1", "ARTICLED_ASSISTANT", 12),
}

STAFF_STATUS_MAP: dict[str, str] = {"Active": "ACTIVE", "Left": "EXITED"}

WORK_LOCATIONS: list[str] = ["Hyderabad", "Bangalore", "Mumbai", "Chennai", "Delhi"]
CITY_STATE: dict[str, str] = {
    "Hyderabad": "Telangana", "Bangalore": "Karnataka", "Mumbai": "Maharashtra",
    "Chennai": "Tamil Nadu", "Delhi": "Delhi",
}

ENTITY_TYPE_MAP: dict[str, bool] = {"Listed": True, "Non-Listed": False}

NATURE_MAP: dict[str, str] = {
    "Private": "PRIVATE",
    "Public": "UNLISTED_PUBLIC",
    "LLP": "LLP",
    "Section 8": "SECTION_8",
    "NBFC": "NBFC",
    "Banking": "BANK",
    "Insurance": "INSURANCE",
    "Trust": "TRUST",
    "Co-operative society": "COOPERATIVE_SOCIETY",
    "Sole proprietorship": "SOLE_PROPRIETORSHIP",
    "Partnership": "PARTNERSHIP_FIRM",
    "Others": "OTHERS",
}

ENGAGEMENT_TYPE_MAP: dict[str, str] = {
    "Statutory audit": "STATUTORY_AUDIT",
    "Limited review": "LIMITED_REVIEW",
    "Internal audit": "INTERNAL_AUDIT",
    "Tax audit": "TAX_AUDIT",
    "GST Audit": "GST_AUDIT",
    "ITR": "ITR",
    "Tax works": "TAX_WORKS",
    "Consultancy": "CONSULTANCY",
    "Opinion": "OPINION",
    "Others": "OTHER",
}

CLIENT_STATUS_MAP: dict[str, str] = {"Active": "ACTIVE", "Inactive": "INACTIVE"}

PARTNERS: list[str] = [
    "Srinivas Gogineni", "Hitesh Kumar P", "Ranganayakulu B", "Sudarshan Gupta MS",
    "Bhargava Anumolu", "Chandrshekar B", "Krishnamohan Reddy JS",
]

PRIORITY_MAP: dict[str, str] = {"High": "HIGH", "Medium": "MEDIUM", "Low": "LOW"}
YES_NO_MAP: dict[str, bool] = {"Yes": True, "No": False}
FIRMS: list[str] = ["BCO", "KSR"]


def lookup(label_map: dict, value: str):
    """Case-insensitive lookup by label; returns None if there's no match."""
    if value is None:
        return None
    value = value.strip()
    for label, mapped in label_map.items():
        if label.lower() == value.lower():
            return mapped
    return None
