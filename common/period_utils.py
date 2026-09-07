"""Period/month normalization helpers shared across statutory-compliance tools.

Extracted out of Combined_PF_Statutory.py so other tools (the Tally GST/TDS
summaries in tally_tool/reports/) can reuse the same "Month" label format
without importing a Streamlit script (which would run its st.set_page_config()
call and other top-level UI code as an import side effect).

Combined_PF_Statutory.py re-exports these under its original names
(_normalize_period, normalize_period, _MONTH_NUM) so nothing there changes
behavior -- this is a pure extraction, not a rewrite.
"""

from __future__ import annotations

import datetime
import re

_MONTH_NUM = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}


def normalize_period(raw) -> str:
    """Parses a free-text period string (as extracted from a PDF -- e.g.
    "Jan-24", "January-2024", "01-2024") into a consistent "%b-%Y" label
    (e.g. "Jan-2024"). Returns the raw string unchanged if nothing matches,
    same as the original Combined_PF_Statutory.py behavior."""
    if not raw:
        return ""
    raw = str(raw).strip()
    cleaned = re.sub(r'\s*[-–]\s*', '-', raw)
    for candidate in [cleaned, cleaned.title(), raw, raw.title()]:
        for fmt in ["%b-%y", "%b-%Y", "%B-%Y", "%B-%y", "%m-%Y", "%B"]:
            try:
                dt = datetime.datetime.strptime(candidate, fmt)
                return candidate.title() if fmt == "%B" else dt.strftime("%b-%Y")
            except Exception:
                pass
    return raw


def month_label(d) -> str:
    """Formats a datetime.date/datetime as the same "%b-%Y" label
    normalize_period() produces (e.g. "Jan-2024"), so a Tally-derived
    row's Date column groups into the same Month key a filed-return's
    normalized "Tax Period" would -- the join key Phase 2's GST/TDS
    reconciliation will need. Returns "" for None/NaT."""
    if d is None:
        return ""
    try:
        if hasattr(d, "isoformat") and (d != d):  # pandas NaT: NaT != NaT
            return ""
    except Exception:
        pass
    try:
        return d.strftime("%b-%Y")
    except Exception:
        return ""
