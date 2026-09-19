"""Tests for the Agreement Terms Summary tool (common/agreement_extractor.py,
_pages/agreement_summary.py): real-world-style extraction against a
synthetic service agreement, plus the simpler lease-style phrasing (this
extractor is meant to generalize beyond the lease-specific tool).
"""
from __future__ import annotations

import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from common.agreement_extractor import STANDARD_CLAUSES, extract_agreement_terms

SERVICE_AGREEMENT_TEXT = """
MASTER SERVICE AGREEMENT

THIS SERVICE AGREEMENT is made and executed on this 15th day of June, 2024
between Delta Consulting Services Pvt Ltd, a company incorporated under the
Companies Act, having its registered office at Pune (hereinafter referred to
as the "Service Provider") of the ONE PART

AND

Omega Retail Ltd, a company incorporated under the Companies Act (hereinafter
referred to as the "Client") of the OTHER PART.

Effective Date: 01/07/2024
Term of this Agreement shall be 2 years.
Expiry Date: 30/06/2026

This Agreement shall automatically renew for successive periods of 1 year
unless either party provides 60 days written notice of non-renewal.

Either party may terminate this Agreement by giving 30 days prior written
notice to the other party.

Fees shall be Rs. 5,00,000 payable monthly in advance.

This Agreement shall be governed by the laws of India.
The courts at Mumbai shall have exclusive jurisdiction.

Both parties agree to keep all information confidential.
The Service Provider shall indemnify the Client against any losses.
Any dispute shall be resolved through arbitration in accordance with the
Arbitration and Conciliation Act.
This Agreement shall not be assigned without prior written consent.
"""

SIMPLE_LEASE_STYLE_TEXT = """
This Lease Deed is entered into between Acme Properties Pvt Ltd (hereinafter
Lessor) and Contoso Industries Ltd (hereinafter Lessee).
"""


def test_extract_service_agreement_parties_and_roles():
    """Regression guard for two real extraction bugs found while building
    this: (1) the role capture matched any nearby capitalised phrase
    ("Companies" from "the Companies Act") instead of requiring an actual
    "(hereinafter ...)" tag to be present, and (2) a non-greedy role
    capture stopped at the first word of a multi-word role ("Service"
    instead of "Service Provider")."""
    terms = extract_agreement_terms(SERVICE_AGREEMENT_TEXT)
    assert terms.party_a == "Delta Consulting Services Pvt Ltd"
    assert terms.party_a_role == "Service Provider"
    assert terms.party_b == "Omega Retail Ltd"
    assert terms.party_b_role == "Client"


def test_extract_service_agreement_dates_and_payment():
    terms = extract_agreement_terms(SERVICE_AGREEMENT_TEXT)
    assert terms.agreement_title == "Service Agreement"
    assert terms.effective_date == "01/07/2024"
    assert terms.expiry_date == "30/06/2026"
    assert terms.term_raw == "2 years"
    assert terms.payment_amount == 500000.0
    assert terms.payment_frequency == "Monthly"
    assert terms.governing_law == "India"
    assert terms.jurisdiction == "Mumbai"
    assert terms.warnings == []


def test_extract_service_agreement_clause_checklist():
    terms = extract_agreement_terms(SERVICE_AGREEMENT_TEXT)
    expected_present = {
        "Confidentiality", "Indemnity", "Termination", "Renewal",
        "Governing Law", "Dispute Resolution / Arbitration", "Assignment",
    }
    expected_absent = {"Force Majeure", "Limitation of Liability", "Non-Compete / Non-Solicit", "Notices"}
    for label in expected_present:
        assert terms.clauses_present[label] is True, f"{label} should be detected as present"
    for label in expected_absent:
        assert terms.clauses_present[label] is False, f"{label} should be detected as absent"
    assert set(terms.clauses_present) == set(STANDARD_CLAUSES)


def test_extract_generalizes_to_lease_style_short_phrasing():
    """This extractor is meant to work across contract types, including
    the same short "(hereinafter Lessor)" phrasing (no quotes, no
    "referred to as") the lease-specific tool handles."""
    terms = extract_agreement_terms(SIMPLE_LEASE_STYLE_TEXT)
    assert terms.party_a == "Acme Properties Pvt Ltd"
    assert terms.party_a_role == "Lessor"
    assert terms.party_b == "Contoso Industries Ltd"
    assert terms.party_b_role == "Lessee"


def test_extract_missing_fields_produce_warnings():
    terms = extract_agreement_terms("This document mentions nothing extractable.")
    assert terms.party_a == ""
    assert terms.party_b == ""
    assert len(terms.warnings) >= 2


def test_agreement_summary_page_calls_real_functions():
    src = open(os.path.join(REPO_ROOT, "_pages", "agreement_summary.py"), encoding="utf-8").read()
    assert "extract_agreement_terms(" in src
    assert "STANDARD_CLAUSES" in src
