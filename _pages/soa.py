"""Hub page: SOA / RPS / Reconcile (Flask app launcher)."""
import streamlit as st

st.title("SOA · RPS · Reconcile")
st.caption("L&T Finance NBFC Statement-of-Account extractor & TOC/TOD audit tool")

st.info(
    "This tool ships as a dedicated **Flask** web app (`app.py`) with live "
    "Server-Sent-Events progress streaming, which runs outside the Streamlit hub.",
    icon="ℹ️",
)
st.markdown(
    """
**Launch it separately:**
```bash
python app.py            # then open http://127.0.0.1:5000
# custom port:
PORT=8080 python app.py
```

**What it does**
- **SOA mode** — per-loan working-paper workbooks + Portfolio Exception Report with TOC/TOD validation.
- **RPS mode** — one combined workbook (`Loan_Details`, `Repayment_Schedule`) from all uploaded schedules.
- **Reconcile mode** — auto-detects SOAs vs RPS, matches by Agreement No, and reports where actual
  servicing deviated from the scheduled plan.

**Command line**
```bash
python extract_soa.py SOA_xxxx.pdf [output.xlsx]      # single loan
python extract_soa.py --dir ./pdfs ./out              # one workbook per loan
python extract_soa.py --portfolio ./pdfs report.xlsx  # portfolio exception report
```
"""
)
