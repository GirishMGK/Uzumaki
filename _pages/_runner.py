"""
Shared helper: run an existing stand-alone Streamlit script as a hub page.

The legacy tool scripts each call ``st.set_page_config`` at import time, which
is only valid on the hub's entry script. We neutralise that call, then execute
the target module fresh (Streamlit reruns page scripts on every interaction).
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
        runpy.run_path(os.path.join(_REPO_ROOT, filename), run_name="__hub_page__")
    finally:
        st.set_page_config = _orig
