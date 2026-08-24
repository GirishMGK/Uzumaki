"""
Shared helper: run an existing stand-alone Streamlit script as a hub page.

The legacy tool scripts each call ``st.set_page_config`` at import time, which
is only valid on the hub's entry script. We neutralise that call, then execute
the target module fresh (Streamlit reruns page scripts on every interaction).

Uses run_name="__main__": some of these scripts (pdf_tools.py) gate their
actual page-dispatch call behind `if __name__ == "__main__":`, since they
were originally standalone `streamlit run pdf_tools.py` apps. Any other
run_name value leaves that guard permanently False and the page silently
renders nothing beyond its own CSS injection and function definitions --
no exception, no error box, just a blank page (found via DOM inspection,
not an HTTP status check, which returns 200 either way).
"""

from __future__ import annotations

import os
import runpy

import streamlit as st

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_script(filename: str) -> None:
    _orig = st.set_page_config
    st.set_page_config = lambda *a, **k: None          # no-op on sub-pages
    try:
        runpy.run_path(os.path.join(_REPO_ROOT, filename), run_name="__main__")
    finally:
        st.set_page_config = _orig
