"""Hub page: JE Audit Analytics Tool — Blazor Server edition (info/launcher)."""
import streamlit as st

from _pages.theme import page_header, footer

page_header(
    "⚡", "JE Audit Analytics Tool — Blazor Edition",
    "Self-contained .NET/Blazor Server port of the JE Audit Analytics Tool",
    badges=[".NET / Blazor Server", "EPPlus", "Runs outside this hub"],
)

st.info(
    "This is a separate **Blazor Server** (.NET) implementation of the same "
    "Journal Entry audit engine, for teams that prefer a C#/.NET deployment "
    "over Python/Streamlit. It runs outside this hub as its own web app.",
    icon="ℹ️",
)
st.markdown(
    """
**Location:** `je_audit_tool_blazor/JEAuditApp.razor`

**Setup**
1. Add the component to a Blazor Server project (`@page "/je-audit"`).
2. Install NuGet packages:
   - `EPPlus` (5.x or 6.x, non-commercial license) — for Excel read/write
   - `Microsoft.AspNetCore.Components.Forms` (included in the Blazor Server SDK)
3. Add to `_Host.cshtml` / `App.razor`:
   ```html
   <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
   ```
4. Run the Blazor Server project — navigate to `/je-audit`.

**What it does** — same 7-step workflow as the Python version: Upload Data →
Column Mapping → Audit Parameters → Run JE Tests → Exception Results →
Dashboard → Export Results. Amount, Timing, User & Access Control, Vendor
Master Data, and Benford's Law tests are implemented natively in C#; charts
render via Plotly.js through JS interop.

**Known stubs** (marked in code comments):
- Excel import/export needs the EPPlus calls uncommented once the NuGet
  package is installed (falls back to CSV parsing / alert otherwise).
- ML Anomaly Detection is a top-5%-by-amount placeholder — swap in
  `Microsoft.ML`'s `IsolationForest` for the full implementation.

This is a UI/logic parity port, not a byte-for-byte translation — the Python
version (`je_audit_tool/`) has the more complete test coverage (18+ test IDs
across amount/timing/user/vendor/Benford, DuckDB-backed for large datasets).
"""
)

footer()
