"""Regex-based best-effort extraction of lease terms from a lease/rent
agreement PDF, plus the two lease-expense-recognition calculations that
follow from those terms: IGAAP (AS 19, straight-line operating lease
expense) and Ind AS 116 (right-of-use asset / lease liability, discounted
at a user-supplied incremental borrowing rate).

Lease agreements are NOT a standardized form the way a PF challan or a
GSTR-1 filing is -- wording, clause order, and terminology vary a lot
between drafters, landlords, and states. Regex extraction here is
deliberately best-effort: every extracted field is meant to be shown back
to the user for review/correction before anything is computed from it,
not trusted blindly. A field regex doesn't match just comes back empty
for the user to fill in themselves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import fitz


def read_pdf_text(file_bytes: bytes) -> str:
    pdf = fitz.open(stream=file_bytes, filetype="pdf")
    return "\n".join(page.get_text(sort=True) for page in pdf)


def _g(pattern: str, text: str, flags=re.I | re.S) -> str:
    m = re.search(pattern, text, flags)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def _num(raw: str) -> float | None:
    if not raw:
        return None
    cleaned = re.sub(r"[,\s₹]", "", raw)
    cleaned = re.sub(r"^(?:Rs\.?|INR)", "", cleaned, flags=re.I)
    try:
        return float(cleaned)
    except ValueError:
        return None


@dataclass
class LeaseTerms:
    lessor: str = ""
    lessee: str = ""
    premises: str = ""
    commencement_date: str = ""
    expiry_date: str = ""
    lease_term_raw: str = ""
    lease_term_months: float | None = None
    lock_in_raw: str = ""
    lock_in_months: float | None = None
    renewal_raw: str = ""
    monthly_rent: float | None = None
    escalation_pct: float | None = None
    escalation_every_years: float | None = None
    security_deposit: float | None = None
    rent_free_period_raw: str = ""
    payment_frequency: str = ""
    warnings: list[str] = field(default_factory=list)


def _duration_to_months(qty: str, unit: str) -> float | None:
    try:
        n = float(qty)
    except ValueError:
        return None
    unit = unit.lower()
    if unit.startswith("y"):
        return n * 12
    if unit.startswith("m"):
        return n
    return None


def extract_lease_terms(text: str) -> LeaseTerms:
    terms = LeaseTerms()

    # Two common phrasings: "Lessor: Acme Pvt Ltd" (label first) and
    # "Acme Pvt Ltd ... (hereinafter referred to as the Lessor)" (name
    # first, the far more common one in actual Indian lease deeds). For the
    # name-first form, restrict each captured word to something that looks
    # like part of a company/person name (capitalised, or a small whitelist
    # of connectors like "Pvt"/"Ltd"/"and") rather than allowing any
    # lowercase word -- otherwise it swallows surrounding prose ("This
    # Lease Deed is entered into between ...") as part of the name.
    # Restrict the match to start right after a party-introducing connector
    # ("between"/"and"/"AND"/"amongst") -- without this, a forward search
    # for "<name tokens> ... (hereinafter ... Lessor)" happily matches the
    # nearest earlier capitalised phrase in the surrounding boilerplate
    # (e.g. "Companies Act" in "... incorporated under the Companies Act,
    # having its office at Mumbai (hereinafter ... Lessor)"), since regular
    # legal prose is full of capitalised phrases. Anchoring on the
    # connector that actually introduces each party is far more reliable
    # than searching backward from the role keyword alone.
    _NAME_TOKEN = r"(?:[A-Z][\w&.'’-]*|Pvt|Ltd|LLP|Inc|Private|Limited|and|of|Co|Company|Corporation)"

    def _name_before(role_pattern: str) -> str:
        pattern = (
            r"(?:between|amongst|and|AND)\s+"
            rf"((?:{_NAME_TOKEN}\.?\s+){{1,6}}{_NAME_TOKEN}\.?)"
            # an optional descriptive clause between the name and the
            # "(hereinafter ...)" tag -- e.g. ", a company incorporated
            # under the Companies Act, having its office at Mumbai" --
            # stopped from swallowing too much by excluding another
            # "Lessor"/"Lessee"/"Tenant"/"Landlord" mention and capped in
            # length, since real boilerplate here can run long.
            r"(?:,\s*(?:(?!Lessor|Lessee|Tenant|Landlord)[^()]){0,200}?)?"
            r"\s*(?:\([^()]{0,120}\)\s*)?\(?\s*(?:hereinafter\s+)?"
            rf"(?:referred\s+to\s+as\s+)?[\"'’]?(?:the\s+)?[\"'’]?{role_pattern}[\"'’]?\)?"
        )
        m = re.search(pattern, text, re.S)
        return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""

    terms.lessor = _name_before("Lessor") or _g(
        r"(?:Lessor|Landlord|Owner)\s*[:\-]?\s*(?:M/s\.?\s*)?([A-Z][A-Za-z0-9&.,'’()\s]{2,80}?)"
        r"(?:\n|,\s*(?:hereinafter|residing|having|represented))",
        text,
    )
    terms.lessee = _name_before("(?:Lessee|Tenant)") or _g(
        r"(?:Lessee|Tenant)\s*[:\-]?\s*(?:M/s\.?\s*)?([A-Z][A-Za-z0-9&.,'’()\s]{2,80}?)"
        r"(?:\n|,\s*(?:hereinafter|residing|having|represented))",
        text,
    )
    terms.premises = _g(
        r"(?:Demised\s+Premises|Leased\s+Premises|Schedule\s+(?:of\s+)?Property|Premises)"
        r"\s*[:\-]?\s*([^\n]{5,200})",
        text,
    )

    terms.commencement_date = _g(
        r"(?:Lease\s+)?Commencement\s+Date\s*[:\-]?\s*"
        r"(\d{1,2}[\/\.\-]\d{1,2}[\/\.\-]\d{2,4}|[A-Za-z]+\s+\d{1,2},?\s+\d{4})",
        text,
    )
    terms.expiry_date = _g(
        r"(?:Lease\s+)?(?:Expiry|Expiration|End|Termination)\s+Date\s*[:\-]?\s*"
        r"(\d{1,2}[\/\.\-]\d{1,2}[\/\.\-]\d{2,4}|[A-Za-z]+\s+\d{1,2},?\s+\d{4})",
        text,
    )

    m = re.search(
        r"(?:Lease\s+)?(?:Period|Term|Tenure)\s+of\s+(?:the\s+)?Lease\s*(?:is|shall\s+be|:)?\s*"
        r"([\d.]+)\s*\(?[A-Za-z\s]*\)?\s*(year|yr|month|mo)s?",
        text, re.I,
    ) or re.search(
        r"Lease\s+(?:Period|Term|Tenure)\s*[:\-]?\s*([\d.]+)\s*(year|yr|month|mo)s?",
        text, re.I,
    )
    if m:
        terms.lease_term_raw = f"{m.group(1)} {m.group(2)}(s)"
        terms.lease_term_months = _duration_to_months(m.group(1), m.group(2))

    m = re.search(
        r"Lock[- ]?in\s*(?:Period)?\s*(?:of|is|shall\s+be|:)?\s*([\d.]+)\s*(year|yr|month|mo)s?",
        text, re.I,
    )
    if m:
        terms.lock_in_raw = f"{m.group(1)} {m.group(2)}(s)"
        terms.lock_in_months = _duration_to_months(m.group(1), m.group(2))

    terms.renewal_raw = _g(
        r"(Renew\w*[^.\n]{0,200}(?:option|period|further\s+term)[^.\n]{0,150})", text
    )

    m = re.search(
        r"(?:Monthly\s+Rent|Rent\s+(?:per\s+month|p\.?m\.?)|Basic\s+Rent)\s*(?:of|is|shall\s+be|:)?\s*"
        r"(?:Rs\.?|INR|₹)?\s*([\d,]+(?:\.\d+)?)",
        text, re.I,
    )
    if m:
        terms.monthly_rent = _num(m.group(1))

    m = re.search(
        r"escalat\w*[^.\n]{0,120}?([\d]{1,2}(?:\.\d+)?)\s*%[^.\n]{0,60}?"
        r"(?:every|per|once\s+in)\s+([\d]+)\s*year",
        text, re.I,
    )
    if m:
        terms.escalation_pct = _num(m.group(1))
        terms.escalation_every_years = _num(m.group(2))

    m = re.search(
        r"Security\s+Deposit\s*(?:of|is|shall\s+be|:)?\s*(?:Rs\.?|INR|₹)?\s*([\d,]+(?:\.\d+)?)",
        text, re.I,
    )
    if m:
        terms.security_deposit = _num(m.group(1))

    terms.rent_free_period_raw = _g(
        r"(Rent[- ]?[Ff]ree\s+Period[^.\n]{0,120}|Fit[- ]?[Oo]ut\s+Period[^.\n]{0,120})", text
    )

    m = re.search(r"rent\s+shall\s+be\s+paid\s+(monthly|quarterly|annually|yearly)", text, re.I)
    terms.payment_frequency = m.group(1).title() if m else ""

    if not terms.lease_term_months:
        terms.warnings.append("Lease term/duration not detected — enter it manually below.")
    if terms.monthly_rent is None:
        terms.warnings.append("Monthly rent not detected — enter it manually below.")
    if not terms.commencement_date:
        terms.warnings.append("Commencement date not detected — enter it manually below.")

    return terms


# ── IGAAP (AS 19) — straight-line operating lease expense ─────────────────────

def igaap_straight_line(
    monthly_rent: float,
    lease_term_months: int,
    escalation_pct: float = 0.0,
    escalation_every_months: int = 0,
) -> dict:
    """AS 19 treats most Indian real-estate leases as operating leases:
    total lease payments over the lease term are recognised on a
    straight-line basis over the term, regardless of the actual payment
    schedule (this is exactly what smooths out a fixed % escalation, since
    AS 19 explicitly does NOT allow straight-lining to be skipped just
    because increases are structured to compensate for expected general
    inflation)."""
    monthly_payments = []
    rent = monthly_rent
    for m in range(lease_term_months):
        if escalation_every_months and m > 0 and m % escalation_every_months == 0:
            rent = rent * (1 + escalation_pct / 100)
        monthly_payments.append(rent)

    total_payments = sum(monthly_payments)
    straight_line_monthly = total_payments / lease_term_months if lease_term_months else 0.0
    return {
        "total_lease_payments": total_payments,
        "lease_term_months": lease_term_months,
        "straight_line_monthly_expense": straight_line_monthly,
        "straight_line_annual_expense": straight_line_monthly * 12,
        "monthly_payment_schedule": monthly_payments,
    }


# ── Ind AS 116 — right-of-use asset / lease liability ──────────────────────────

def ind_as116_rou_and_liability(
    monthly_rent: float,
    lease_term_months: int,
    annual_discount_rate_pct: float,
    escalation_pct: float = 0.0,
    escalation_every_months: int = 0,
    initial_direct_costs: float = 0.0,
) -> dict:
    """Ind AS 116: lessee recognises a right-of-use asset and a lease
    liability at the present value of remaining lease payments, discounted
    at the incremental borrowing rate (IBR) -- since that rate is a
    judgement/accounting-policy input almost never stated verbatim in the
    lease deed itself, it must be supplied by the user, not extracted.
    Expense recognition changes character: straight-line rent is replaced
    by depreciation of the ROU asset (typically straight-line over the
    lease term) plus interest expense on the unwinding lease liability
    (front-loaded, higher in earlier periods)."""
    monthly_rate = (1 + annual_discount_rate_pct / 100) ** (1 / 12) - 1

    rent = monthly_rent
    payments = []
    for m in range(lease_term_months):
        if escalation_every_months and m > 0 and m % escalation_every_months == 0:
            rent = rent * (1 + escalation_pct / 100)
        payments.append(rent)

    pv_lease_liability = sum(
        pmt / ((1 + monthly_rate) ** (i + 1)) for i, pmt in enumerate(payments)
    )
    rou_asset_initial = pv_lease_liability + initial_direct_costs
    monthly_depreciation = rou_asset_initial / lease_term_months if lease_term_months else 0.0

    schedule = []
    liability = pv_lease_liability
    for i, pmt in enumerate(payments):
        interest = liability * monthly_rate
        principal = pmt - interest
        liability = max(liability - principal, 0.0)
        schedule.append({
            "Month": i + 1,
            "Opening Liability": None,  # filled below once we know the opening balance
            "Interest": interest,
            "Payment": pmt,
            "Principal": principal,
            "Closing Liability": liability,
            "Depreciation": monthly_depreciation,
        })
    # backfill opening liability now that closing balances are known
    opening = pv_lease_liability
    for row in schedule:
        row["Opening Liability"] = opening
        opening = row["Closing Liability"]

    year1 = schedule[:12]
    return {
        "discount_rate_annual_pct": annual_discount_rate_pct,
        "pv_lease_liability_initial": pv_lease_liability,
        "rou_asset_initial": rou_asset_initial,
        "monthly_depreciation": monthly_depreciation,
        "year1_interest_expense": sum(r["Interest"] for r in year1),
        "year1_depreciation_expense": sum(r["Depreciation"] for r in year1),
        "year1_total_expense": sum(r["Interest"] + r["Depreciation"] for r in year1),
        "amortization_schedule": schedule,
    }
