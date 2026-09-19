"""Hub page: Lease Terms Summary.

Reads an uploaded lease/rent agreement PDF, best-effort extracts the terms
lease-expense recognition depends on (regex over the PDF's raw text -- see
common/lease_extractor.py's module docstring for why this is deliberately
best-effort rather than a promise of correctness), shows them back in an
editable form for the user to verify/correct, then computes:
  - IGAAP (AS 19): straight-line operating lease expense over the term
  - Ind AS 116: right-of-use asset / lease liability, amortisation schedule
    (needs a discount rate/IBR input -- that's a judgement call almost
    never stated verbatim in the agreement, so it's never auto-extracted)

Lease agreements aren't a standardized form the way a PF challan or GSTR-1
filing is, so this is explicitly a draftsperson's starting point, not a
substitute for reading the agreement -- every extracted field is editable
before anything is computed from it.
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

from _pages.theme import page_header, footer
from common.lease_extractor import (
    extract_lease_terms,
    igaap_straight_line,
    ind_as116_rou_and_liability,
    read_pdf_text,
)

page_header(
    "📑", "Lease Terms Summary",
    "Upload a lease/rent agreement — extracts the key terms and computes "
    "lease-expense recognition under IGAAP (AS 19, straight-line) and "
    "Ind AS 116 (right-of-use asset / lease liability).",
    badges=["Regex extraction — verify before use", "AS 19", "Ind AS 116"],
)

st.info(
    "Lease agreements aren't a standardized form, so extraction here is "
    "**best-effort** — always verify every field below against the actual "
    "agreement before relying on the computed figures.",
    icon="⚠️",
)

uploaded = st.file_uploader("Upload the lease agreement (PDF)", type="pdf", key="lease_pdf")

if uploaded:
    if st.session_state.get("lease_src_name") != uploaded.name:
        text = read_pdf_text(uploaded.read())
        st.session_state.lease_terms = extract_lease_terms(text)
        st.session_state.lease_src_name = uploaded.name

    terms = st.session_state.lease_terms

    if terms.warnings:
        for w in terms.warnings:
            st.warning(w, icon="✏️")

    st.markdown("#### 1. Extracted terms — review and correct")
    c1, c2 = st.columns(2)
    with c1:
        lessor = st.text_input("Lessor", terms.lessor)
        premises = st.text_input("Premises", terms.premises)
        commencement = st.text_input("Commencement date", terms.commencement_date)
        lease_term_months = st.number_input(
            "Lease term (months)", min_value=0.0,
            value=float(terms.lease_term_months or 0), step=1.0,
        )
        lock_in_months = st.number_input(
            "Lock-in period (months, 0 if none)", min_value=0.0,
            value=float(terms.lock_in_months or 0), step=1.0,
        )
    with c2:
        lessee = st.text_input("Lessee", terms.lessee)
        expiry = st.text_input("Expiry date", terms.expiry_date)
        monthly_rent = st.number_input(
            "Monthly rent (₹)", min_value=0.0, value=float(terms.monthly_rent or 0), step=1000.0
        )
        escalation_pct = st.number_input(
            "Rent escalation (%)", min_value=0.0, value=float(terms.escalation_pct or 0), step=0.5
        )
        escalation_every_years = st.number_input(
            "Escalation every (years, 0 if flat rent)", min_value=0.0,
            value=float(terms.escalation_every_years or 0), step=1.0,
        )
    security_deposit = st.number_input(
        "Security deposit (₹)", min_value=0.0, value=float(terms.security_deposit or 0), step=1000.0
    )
    if terms.renewal_raw:
        st.caption(f"Renewal clause (as found): {terms.renewal_raw}")
    if terms.rent_free_period_raw:
        st.caption(f"Rent-free/fit-out period (as found): {terms.rent_free_period_raw}")

    st.markdown("#### 2. Ind AS 116 discount rate")
    discount_rate = st.number_input(
        "Incremental borrowing rate (annual %) — not extractable from the "
        "agreement itself, enter your policy rate",
        min_value=0.0, value=10.0, step=0.25,
    )

    if st.button("Compute lease-expense summary", type="primary"):
        if lease_term_months <= 0 or monthly_rent <= 0:
            st.error("Lease term and monthly rent must both be greater than zero.")
        else:
            term_months = int(lease_term_months)
            esc_every_months = int(escalation_every_years * 12)

            igaap = igaap_straight_line(
                monthly_rent=monthly_rent,
                lease_term_months=term_months,
                escalation_pct=escalation_pct,
                escalation_every_months=esc_every_months,
            )
            ind_as = ind_as116_rou_and_liability(
                monthly_rent=monthly_rent,
                lease_term_months=term_months,
                annual_discount_rate_pct=discount_rate,
                escalation_pct=escalation_pct,
                escalation_every_months=esc_every_months,
            )

            st.session_state.lease_result = {
                "terms": {
                    "Lessor": lessor, "Lessee": lessee, "Premises": premises,
                    "Commencement Date": commencement, "Expiry Date": expiry,
                    "Lease Term (months)": term_months,
                    "Lock-in (months)": lock_in_months,
                    "Monthly Rent (₹)": monthly_rent,
                    "Escalation (%)": escalation_pct,
                    "Escalation Every (years)": escalation_every_years,
                    "Security Deposit (₹)": security_deposit,
                },
                "igaap": igaap,
                "ind_as": ind_as,
            }

    if "lease_result" in st.session_state:
        result = st.session_state.lease_result
        igaap, ind_as = result["igaap"], result["ind_as"]

        st.markdown("#### 3. IGAAP (AS 19) — operating lease, straight-line")
        k1, k2, k3 = st.columns(3)
        k1.metric("Total lease payments", f"₹{igaap['total_lease_payments']:,.0f}")
        k2.metric("Straight-line monthly expense", f"₹{igaap['straight_line_monthly_expense']:,.0f}")
        k3.metric("Straight-line annual expense", f"₹{igaap['straight_line_annual_expense']:,.0f}")
        st.caption(
            "AS 19 requires operating-lease payments to be recognised on a "
            "straight-line basis over the lease term, even where a fixed % "
            "escalation is structured to compensate for expected general "
            "inflation — that's exactly what smoothing does here."
        )

        st.markdown("#### 4. Ind AS 116 — right-of-use asset / lease liability")
        k1, k2, k3 = st.columns(3)
        k1.metric("Initial lease liability (PV)", f"₹{ind_as['pv_lease_liability_initial']:,.0f}")
        k2.metric("Initial ROU asset", f"₹{ind_as['rou_asset_initial']:,.0f}")
        k3.metric("Monthly depreciation", f"₹{ind_as['monthly_depreciation']:,.0f}")
        k1, k2, k3 = st.columns(3)
        k1.metric("Year 1 interest expense", f"₹{ind_as['year1_interest_expense']:,.0f}")
        k2.metric("Year 1 depreciation expense", f"₹{ind_as['year1_depreciation_expense']:,.0f}")
        k3.metric("Year 1 total P&L impact", f"₹{ind_as['year1_total_expense']:,.0f}")
        st.caption(
            f"Discounted at {ind_as['discount_rate_annual_pct']:.2f}% p.a. (IBR). "
            "Depreciation is straight-line over the lease term; interest "
            "unwinds the liability and is front-loaded — higher in earlier "
            "periods, unlike AS 19's flat straight-line rent expense."
        )

        with st.expander("Ind AS 116 monthly amortisation schedule"):
            sched_df = pd.DataFrame(ind_as["amortization_schedule"])
            st.dataframe(sched_df, use_container_width=True)

        buf = BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            pd.DataFrame([result["terms"]]).T.rename(columns={0: "Value"}).to_excel(w, sheet_name="Lease Terms")
            pd.DataFrame([{
                "Total Lease Payments": igaap["total_lease_payments"],
                "Lease Term (months)": igaap["lease_term_months"],
                "Straight-line Monthly Expense": igaap["straight_line_monthly_expense"],
                "Straight-line Annual Expense": igaap["straight_line_annual_expense"],
            }]).to_excel(w, sheet_name="IGAAP (AS 19)", index=False)
            pd.DataFrame([{
                "Discount Rate (% p.a.)": ind_as["discount_rate_annual_pct"],
                "Initial Lease Liability (PV)": ind_as["pv_lease_liability_initial"],
                "Initial ROU Asset": ind_as["rou_asset_initial"],
                "Monthly Depreciation": ind_as["monthly_depreciation"],
                "Year 1 Interest Expense": ind_as["year1_interest_expense"],
                "Year 1 Depreciation Expense": ind_as["year1_depreciation_expense"],
                "Year 1 Total P&L Impact": ind_as["year1_total_expense"],
            }]).to_excel(w, sheet_name="Ind AS 116 Summary", index=False)
            pd.DataFrame(ind_as["amortization_schedule"]).to_excel(
                w, sheet_name="Ind AS 116 Schedule", index=False
            )
        buf.seek(0)
        st.download_button(
            "⬇️ Download Lease Summary (Excel)", buf,
            file_name="Lease_Terms_Summary.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
        )

with st.expander("What this does / limitations"):
    st.markdown(
        """
Extracts lease terms from an uploaded agreement using regex over the PDF's
raw text — unlike PF/GST/Tally forms, lease agreements have no fixed layout,
so extraction here is best-effort: every field is editable, and a field the
regex misses just comes back blank for you to fill in.

**IGAAP (AS 19):** treats the lease as an operating lease and straight-lines
total payments over the term — the standard treatment for most Indian
real-estate operating leases, including where a fixed % escalation exists.

**Ind AS 116:** every lessee lease (with narrow exemptions for short-term/
low-value leases, not modelled here) is capitalised as a right-of-use asset
and lease liability at the present value of future payments, discounted at
your incremental borrowing rate (IBR) — a policy judgement, so it's always
asked for rather than guessed at or extracted.

Not modelled: variable/contingent rent, CAM charges, multiple linked
leases, lease modifications, short-term (≤12 month) or low-value-asset
exemptions, or sub-leases.
"""
    )

footer()
