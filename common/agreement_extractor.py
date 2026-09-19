"""Regex-based best-effort extraction of general contract/agreement terms
-- unlike common/lease_extractor.py (built for one specific contract type
with a known accounting outcome), this covers arbitrary agreements: service
agreements, NDAs, employment contracts, vendor/purchase agreements,
consultancy agreements, licensing agreements, etc.

Same caveat as the lease extractor, more so here since the document type
itself varies, not just the wording within one type: extraction is
deliberately best-effort, every field is meant to be reviewed/corrected by
the user, and a field the regex doesn't find just comes back empty rather
than guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .pdf_text import read_pdf_text

__all__ = ["read_pdf_text", "extract_agreement_terms", "AgreementTerms", "STANDARD_CLAUSES"]


def _g(pattern: str, text: str, flags=re.I | re.S) -> str:
    m = re.search(pattern, text, flags)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


_AGREEMENT_TYPE_KEYWORDS = [
    "Non-Disclosure Agreement", "Nondisclosure Agreement", "Confidentiality Agreement",
    "Service Agreement", "Master Service Agreement", "Employment Agreement",
    "Consultancy Agreement", "Consulting Agreement", "Vendor Agreement",
    "Purchase Agreement", "Supply Agreement", "License Agreement", "Licence Agreement",
    "Franchise Agreement", "Partnership Agreement", "Shareholders Agreement",
    "Joint Venture Agreement", "Lease Deed", "Lease Agreement", "Rent Agreement",
    "Loan Agreement", "Facility Agreement", "Distribution Agreement",
    "Memorandum of Understanding", "Agreement",
]

@dataclass
class AgreementTerms:
    agreement_title: str = ""
    party_a: str = ""
    party_a_role: str = ""
    party_b: str = ""
    party_b_role: str = ""
    effective_date: str = ""
    term_raw: str = ""
    expiry_date: str = ""
    renewal_raw: str = ""
    termination_notice_raw: str = ""
    payment_amount: float | None = None
    payment_frequency: str = ""
    governing_law: str = ""
    jurisdiction: str = ""
    clauses_present: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


STANDARD_CLAUSES = {
    "Confidentiality": r"\bconfidential(?:ity)?\b",
    "Indemnity": r"\bindemnif\w*\b",
    "Termination": r"\bterminat\w*\b",
    "Renewal": r"\brenew\w*\b",
    "Governing Law": r"governing\s+law|governed\s+by\s+the\s+laws",
    "Dispute Resolution / Arbitration": r"\barbitrat\w*\b|dispute\s+resolution",
    "Force Majeure": r"force\s+majeure",
    "Limitation of Liability": r"limitation\s+of\s+liability|limited\s+liability\s+for",
    "Non-Compete / Non-Solicit": r"non[- ]compete|non[- ]solicit",
    "Assignment": r"\bassign\w*\b",
    "Notices": r"\bnotices?\b\s*[:\-]|manner\s+of\s+notice",
}


def _num(raw: str) -> float | None:
    if not raw:
        return None
    cleaned = re.sub(r"[,\s₹$]", "", raw)
    cleaned = re.sub(r"^(?:Rs\.?|INR|USD)", "", cleaned, flags=re.I)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _name_and_role(text: str) -> tuple[str, str]:
    """Same anchoring technique as lease_extractor._name_before(): a party
    name is far more reliably found by anchoring on the connector word that
    introduces it ("between"/"and"/"AND") than by searching backward from
    a role keyword, since ordinary contract prose is full of other
    capitalised phrases a naive backward search would latch onto instead.

    The role is captured directly from the SAME "(hereinafter ... as the
    "<Role>")" match, as whatever quoted/capitalised phrase is actually
    there -- rather than matched separately against a fixed whitelist of
    role words, which is what previously made a generic prose word like
    "company" (from "... a company incorporated under...") win over the
    real role tag "Service Provider" that came later in the same sentence."""
    name_token = r"(?:[A-Z][\w&.'’-]*|Pvt|Ltd|LLP|Inc|Private|Limited|and|of|Co|Company|Corporation|Mr\.|Mrs\.|Ms\.)"
    # "hereinafter" is required, not optional -- without it, the role group
    # would just match the next capitalised phrase after the description
    # regardless of whether it's actually a role tag at all (that's what
    # let "Companies" from "the Companies Act" win over the real "Service
    # Provider" role, since every other keyword on the way there was
    # marked optional).
    pattern = (
        r"(?:between|amongst|and|AND)\s+"
        rf"((?:{name_token}\.?\s+){{1,6}}{name_token}\.?)"
        r"(?:,\s*(?:(?!hereinafter)[^()]){0,200}?)?"
        r"\s*\(?\s*hereinafter\s+(?:referred\s+to\s+as\s+)?"
        r"[\"'’]?(?:the\s+)?[\"'’]?"
        r"([A-Z][A-Za-z][A-Za-z\s]{1,40})[\"'’]?\)?"
        r"(?=[\s,.)]|$)"
    )
    m = re.search(pattern, text, re.S)
    if not m:
        return "", ""
    name = re.sub(r"\s+", " ", m.group(1)).strip()
    role = re.sub(r"\s+", " ", m.group(2)).strip()
    return name, role


def extract_agreement_terms(text: str) -> AgreementTerms:
    terms = AgreementTerms()

    for kw in _AGREEMENT_TYPE_KEYWORDS:
        if re.search(re.escape(kw), text, re.I):
            terms.agreement_title = kw
            break

    terms.party_a, terms.party_a_role = _name_and_role(text)

    # find the SECOND party by searching in the text that comes after the
    # first party's match, so party B isn't just the same match repeated
    if terms.party_a:
        idx = text.find(terms.party_a)
        rest = text[idx + len(terms.party_a):] if idx >= 0 else text
    else:
        rest = text
    terms.party_b, terms.party_b_role = _name_and_role(rest)

    terms.effective_date = _g(
        r"(?:Effective|Commencement|Execution)\s+Date\s*[:\-]?\s*"
        r"(\d{1,2}[\/\.\-]\d{1,2}[\/\.\-]\d{2,4}|[A-Za-z]+\s+\d{1,2},?\s+\d{4})",
        text,
    ) or _g(
        r"(?:made\s+and\s+executed|entered\s+into)\s+on\s+this\s+"
        r"(\d{1,2}(?:st|nd|rd|th)?\s+day\s+of\s+[A-Za-z]+,?\s+\d{4})",
        text,
    )

    terms.expiry_date = _g(
        r"(?:Expiry|Expiration|End)\s+Date\s*[:\-]?\s*"
        r"(\d{1,2}[\/\.\-]\d{1,2}[\/\.\-]\d{2,4}|[A-Za-z]+\s+\d{1,2},?\s+\d{4})",
        text,
    )

    m = re.search(
        r"(?:Term|Period|Duration)\s+of\s+(?:this\s+)?Agreement\s*(?:is|shall\s+be|:)?\s*"
        r"([\d.]+\s*\(?[A-Za-z\s]*\)?\s*(?:year|yr|month|mo)s?)",
        text, re.I,
    )
    if m:
        terms.term_raw = m.group(1).strip()

    terms.renewal_raw = _g(
        r"(Renew\w*[^.\n]{0,200}(?:option|period|automatically|further\s+term)[^.\n]{0,150})", text
    )
    terms.termination_notice_raw = _g(
        r"(Termination[^.\n]{0,80}(?:notice)[^.\n]{0,150}|"
        r"(?:\d+)\s*(?:days?|months?)\s*(?:prior\s+)?(?:written\s+)?notice[^.\n]{0,100})",
        text,
    )

    m = re.search(
        r"(?:Fees?|Consideration|Payment|Contract\s+Value)\s*(?:of|is|shall\s+be|:)?\s*"
        r"(?:Rs\.?|INR|₹|\$|USD)\s*([\d,]+(?:\.\d+)?)",
        text, re.I,
    )
    if m:
        terms.payment_amount = _num(m.group(1))

    m = re.search(
        r"(?:payable|paid)\s+(monthly|quarterly|annually|yearly|one[- ]time|in\s+advance|upon\s+completion)",
        text, re.I,
    )
    terms.payment_frequency = m.group(1).title() if m else ""

    terms.governing_law = _g(
        r"(?:governed\s+by\s+the\s+laws\s+of|Governing\s+Law\s*[:\-]?)\s*([^\n,\.]{3,80})",
        text,
    )
    terms.jurisdiction = _g(
        r"courts?\s+(?:at|of|in)\s+([^\n,\.]{3,60})\s*shall\s+have\s+(?:exclusive\s+)?jurisdiction",
        text,
    )

    terms.clauses_present = {
        label: bool(re.search(pattern, text, re.I))
        for label, pattern in STANDARD_CLAUSES.items()
    }

    if not terms.party_a or not terms.party_b:
        terms.warnings.append("One or both parties not detected — enter them manually below.")
    if not terms.effective_date:
        terms.warnings.append("Effective/commencement date not detected — enter it manually below.")
    if not terms.term_raw and not terms.expiry_date:
        terms.warnings.append("Term/duration or expiry date not detected — enter manually below.")

    return terms
