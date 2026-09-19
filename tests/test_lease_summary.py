"""Tests for the Lease Terms Summary tool (common/lease_extractor.py,
_pages/lease_summary.py): real-world-style extraction against synthetic
lease deed text (two different common phrasings, since lease agreements
aren't a standardized form the way a PF challan is), and the IGAAP (AS 19)
/ Ind AS 116 calculations against hand-computed expected values.
"""
from __future__ import annotations

import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from common.lease_extractor import (
    extract_lease_terms,
    igaap_straight_line,
    ind_as116_rou_and_liability,
)

SIMPLE_LEASE_TEXT = """
LEASE DEED

This Lease Deed is entered into between Acme Properties Pvt Ltd (hereinafter
Lessor) and Contoso Industries Ltd (hereinafter Lessee).

Demised Premises: Office Unit 4B, Tower 2, Business Park, Bengaluru

Lease Commencement Date: 01/04/2024
Lease Expiry Date: 31/03/2029

Lease Period of the Lease shall be 5 years.
Lock-in Period of 3 years shall apply.

Monthly Rent shall be Rs. 1,00,000 payable in advance.
The rent shall escalate by 5% every 3 years.

Security Deposit of Rs. 3,00,000 shall be paid by the Lessee.

Rent Free Period of 2 months shall be given for fit-out.

Renewal option of 5 years at mutually agreed terms available to the Lessee.

The rent shall be paid monthly in advance.
"""

# Mimics a real Indian lease deed's more verbose boilerplate, including a
# descriptive clause (with a line wrap, as PDF-extracted text often has)
# between each party's name and its "(hereinafter ...)" tag.
BOILERPLATE_LEASE_TEXT = """
THIS DEED OF LEASE is made and executed on this 1st day of April, 2024
between Global Realty Private Limited, a company incorporated under the
Companies Act, having its office at Mumbai (hereinafter referred to as
the "Lessor" which expression shall include its successors and permitted
assigns) of the ONE PART

AND

Beta Software Solutions LLP, a limited liability partnership (hereinafter
referred to as the "Lessee" which expression shall include its successors)
of the OTHER PART.
"""


def test_extract_lease_terms_simple_phrasing():
    terms = extract_lease_terms(SIMPLE_LEASE_TEXT)
    assert terms.lessor == "Acme Properties Pvt Ltd"
    assert terms.lessee == "Contoso Industries Ltd"
    assert "Office Unit 4B" in terms.premises
    assert terms.commencement_date == "01/04/2024"
    assert terms.expiry_date == "31/03/2029"
    assert terms.lease_term_months == 60.0
    assert terms.lock_in_months == 36.0
    assert terms.monthly_rent == 100000.0
    assert terms.escalation_pct == 5.0
    assert terms.escalation_every_years == 3.0
    assert terms.security_deposit == 300000.0
    assert terms.payment_frequency == "Monthly"
    assert terms.warnings == []


def test_extract_lease_terms_real_world_boilerplate():
    """Regression guard: a naive forward "name ... (hereinafter Lessor)"
    regex matched the nearest EARLIER capitalised phrase in the document
    (e.g. "Companies Act" from "... incorporated under the Companies Act,
    having its office at Mumbai (hereinafter ... Lessor)") instead of the
    actual party name, because ordinary legal prose is full of capitalised
    phrases. Fixed by anchoring the match on the connector word that
    actually introduces each party ("between"/"AND"). Also guards a
    second bug: the optional descriptive clause between the name and its
    "(hereinafter ...)" tag excluded newlines, so it silently failed
    whenever that clause wrapped across a line -- exactly what PDF-extracted
    text does for a long sentence."""
    terms = extract_lease_terms(BOILERPLATE_LEASE_TEXT)
    assert terms.lessor == "Global Realty Private Limited"
    assert terms.lessee == "Beta Software Solutions LLP"


def test_extract_lease_terms_missing_fields_produce_warnings():
    terms = extract_lease_terms("This document mentions nothing extractable.")
    assert terms.lease_term_months is None
    assert terms.monthly_rent is None
    assert len(terms.warnings) >= 2


def test_igaap_straight_line_flat_rent():
    result = igaap_straight_line(monthly_rent=50000, lease_term_months=12)
    assert result["total_lease_payments"] == 600000
    assert result["straight_line_monthly_expense"] == 50000
    assert result["straight_line_annual_expense"] == 600000


def test_igaap_straight_line_with_escalation_smooths_to_average():
    # 3 years, 100000/mo flat for yr1, 5% escalation applied at month 12
    # and month 24 (every 12 months) -> yr1 @100000, yr2 @105000, yr3 @110250
    result = igaap_straight_line(
        monthly_rent=100000, lease_term_months=36,
        escalation_pct=5, escalation_every_months=12,
    )
    total = 100000 * 12 + 105000 * 12 + 110250 * 12
    assert result["total_lease_payments"] == total
    # AS 19 straight-lines the whole term -- so the monthly expense must be
    # a single flat number, not tracking the actual escalated payment.
    assert result["straight_line_monthly_expense"] == total / 36


def test_ind_as116_zero_discount_rate_equals_undiscounted_total():
    """At a 0% discount rate, the PV of lease payments must equal their
    undiscounted sum -- a sanity check independent of the PV formula's
    internal correctness."""
    result = ind_as116_rou_and_liability(
        monthly_rent=50000, lease_term_months=12, annual_discount_rate_pct=0.0,
    )
    assert abs(result["pv_lease_liability_initial"] - 600000) < 1e-6
    assert abs(result["rou_asset_initial"] - 600000) < 1e-6


def test_ind_as116_positive_rate_discounts_below_undiscounted_total():
    result = ind_as116_rou_and_liability(
        monthly_rent=50000, lease_term_months=12, annual_discount_rate_pct=10.0,
    )
    assert result["pv_lease_liability_initial"] < 600000
    assert result["pv_lease_liability_initial"] > 0


def test_ind_as116_amortization_schedule_liability_unwinds_to_zero():
    result = ind_as116_rou_and_liability(
        monthly_rent=75000, lease_term_months=24, annual_discount_rate_pct=8.5,
    )
    schedule = result["amortization_schedule"]
    assert len(schedule) == 24
    assert schedule[0]["Opening Liability"] == result["pv_lease_liability_initial"]
    # each month's closing liability feeds the next month's opening
    for i in range(1, len(schedule)):
        assert schedule[i]["Opening Liability"] == schedule[i - 1]["Closing Liability"]
    assert schedule[-1]["Closing Liability"] < 1.0  # fully unwound by lease end


def test_ind_as116_interest_front_loaded_vs_igaap_flat_expense():
    """Real accounting distinction this tool exists to surface: Ind AS 116
    interest is front-loaded (higher in month 1 than month 12 of the same
    year), unlike AS 19's flat straight-line rent expense."""
    result = ind_as116_rou_and_liability(
        monthly_rent=100000, lease_term_months=60, annual_discount_rate_pct=10.0,
    )
    schedule = result["amortization_schedule"]
    assert schedule[0]["Interest"] > schedule[11]["Interest"]


def test_lease_summary_page_calls_real_functions():
    """Structural guard, same pattern as the other hub pages' regression
    tests: the page must actually call the extraction/calculation
    functions, not a placeholder."""
    src = open(os.path.join(REPO_ROOT, "_pages", "lease_summary.py"), encoding="utf-8").read()
    for fn in ["extract_lease_terms(", "igaap_straight_line(", "ind_as116_rou_and_liability("]:
        assert fn in src, f"_pages/lease_summary.py no longer calls {fn}"
