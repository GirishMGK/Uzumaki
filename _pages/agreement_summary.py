"""Hub page: Agreement Terms Summary.

General-purpose sibling of the Lease Terms Summary tool: works on any
contract type (service agreement, NDA, employment, vendor/purchase,
consultancy, licensing, etc.), not just a lease. Extracts the parties,
key dates, payment terms, and flags which standard clauses (confidentiality,
indemnity, termination, arbitration, force majeure, etc.) are present or
missing -- useful as a first-pass review checklist.

Same caveat as the lease tool, more so here since the document type itself
varies: extraction is deliberately best-effort, every field is editable
before export, and a field the regex doesn't find just comes back blank.
"""
from __future__ import annotations

import os
import sys
from io import BytesIO

import pandas as pd
import streamlit as st

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from _pages.theme import footer
from common.agreement_extractor import STANDARD_CLAUSES, extract_agreement_terms, read_pdf_text

st.subheader("📝 Agreement Terms Summary")
st.caption(
    "Upload any contract/agreement — extracts the parties, key dates, and "
    "payment terms, and flags which standard clauses are present or missing."
)
st.info(
    "Contracts aren't a standardized form, so extraction here is "
    "**best-effort** — always verify every field below against the actual "
    "agreement before relying on it.",
    icon="⚠️",
)

uploaded = st.file_uploader("Upload the agreement (PDF)", type="pdf", key="agreement_pdf")

if uploaded:
    if st.session_state.get("agreement_src_name") != uploaded.name:
        text = read_pdf_text(uploaded.read())
        st.session_state.agreement_terms = extract_agreement_terms(text)
        st.session_state.agreement_src_name = uploaded.name

    terms = st.session_state.agreement_terms

    if terms.warnings:
        for w in terms.warnings:
            st.warning(w, icon="✏️")

    st.markdown("#### 1. Extracted terms — review and correct")
    agreement_title = st.text_input("Agreement type/title", terms.agreement_title)

    c1, c2 = st.columns(2)
    with c1:
        party_a = st.text_input("Party A", terms.party_a)
        party_a_role = st.text_input("Party A role", terms.party_a_role)
        effective_date = st.text_input("Effective/commencement date", terms.effective_date)
        term_raw = st.text_input("Term/duration", terms.term_raw)
    with c2:
        party_b = st.text_input("Party B", terms.party_b)
        party_b_role = st.text_input("Party B role", terms.party_b_role)
        expiry_date = st.text_input("Expiry date", terms.expiry_date)
        payment_amount = st.number_input(
            "Payment amount (₹)", min_value=0.0, value=float(terms.payment_amount or 0), step=1000.0
        )

    payment_frequency = st.text_input("Payment frequency", terms.payment_frequency)
    c3, c4 = st.columns(2)
    with c3:
        governing_law = st.text_input("Governing law", terms.governing_law)
    with c4:
        jurisdiction = st.text_input("Jurisdiction", terms.jurisdiction)

    if terms.renewal_raw:
        st.caption(f"Renewal clause (as found): {terms.renewal_raw}")
    if terms.termination_notice_raw:
        st.caption(f"Termination notice (as found): {terms.termination_notice_raw}")

    st.markdown("#### 2. Standard clause checklist")
    st.caption(
        "Keyword-presence check, not a legal review — a clause marked "
        "**Not found** may still exist under different wording; verify "
        "against the actual document."
    )
    clause_cols = st.columns(3)
    clause_state = {}
    for i, label in enumerate(STANDARD_CLAUSES):
        detected = terms.clauses_present.get(label, False)
        with clause_cols[i % 3]:
            clause_state[label] = st.checkbox(label, value=detected, key=f"clause_{label}")

    if st.button("Save summary", type="primary"):
        st.session_state.agreement_result = {
            "Agreement Type": agreement_title,
            "Party A": party_a, "Party A Role": party_a_role,
            "Party B": party_b, "Party B Role": party_b_role,
            "Effective Date": effective_date, "Expiry Date": expiry_date,
            "Term": term_raw,
            "Payment Amount (₹)": payment_amount, "Payment Frequency": payment_frequency,
            "Governing Law": governing_law, "Jurisdiction": jurisdiction,
            "Renewal Clause": terms.renewal_raw,
            "Termination Notice": terms.termination_notice_raw,
        }
        st.session_state.agreement_clauses = clause_state

    if "agreement_result" in st.session_state:
        result = st.session_state.agreement_result
        clauses = st.session_state.agreement_clauses

        st.markdown("#### 3. Summary")
        st.dataframe(
            pd.DataFrame([result]).T.rename(columns={0: "Value"}), use_container_width=True
        )
        present = [c for c, v in clauses.items() if v]
        missing = [c for c, v in clauses.items() if not v]
        if missing:
            st.warning("Not found / not confirmed: " + ", ".join(missing), icon="⚠️")
        if present:
            st.success("Present: " + ", ".join(present), icon="✅")

        buf = BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            pd.DataFrame([result]).T.rename(columns={0: "Value"}).to_excel(w, sheet_name="Agreement Terms")
            pd.DataFrame(
                [{"Clause": c, "Present": v} for c, v in clauses.items()]
            ).to_excel(w, sheet_name="Clause Checklist", index=False)
        buf.seek(0)
        st.download_button(
            "⬇️ Download Agreement Summary (Excel)", buf,
            file_name="Agreement_Terms_Summary.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
        )

with st.expander("What this does / limitations"):
    st.markdown(
        """
Extracts contract terms from an uploaded agreement using regex over the
PDF's raw text. Unlike the Lease Terms Summary tool (built for one specific
contract type with a known accounting outcome), this covers arbitrary
agreements — extraction is correspondingly less reliable, so every field is
editable and the clause checklist is a keyword-presence check, not a legal
review.

**What it extracts:** agreement type/title, both parties and their defined
roles, effective/expiry dates, term, payment amount/frequency, governing
law, jurisdiction, and a checklist of standard clauses (confidentiality,
indemnity, termination, renewal, dispute resolution, force majeure,
limitation of liability, non-compete, assignment, notices).

**Not modelled:** multi-party agreements (more than two parties),
schedules/annexures referenced but not inline, amendments to a prior
agreement, or clause-level obligations beyond presence/absence.
"""
    )

footer()
