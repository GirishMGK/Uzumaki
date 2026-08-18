# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Uzumaki.exe — bundles launcher.py plus every source file
the Streamlit hub needs at runtime, preserving the exact relative directory
layout of the repo (Home.py at the bundle root, _pages/ beside it, etc.) so
none of the app's own sys.path.insert(__file__-relative) calls need to change
for frozen mode.

Build (on Windows, from the repo root):
    pip install -r requirements.txt -r requirements-build.txt
    pyinstaller Uzumaki.spec
Output: dist/Uzumaki.exe
"""

import os

from PyInstaller.utils.hooks import copy_metadata, collect_data_files

# SPECPATH is already the directory containing this spec file (not the file
# path itself) -- PyInstaller sets it that way. dirname()'ing it, as an
# earlier version of this file did, walks one directory too high and breaks
# every path below (e.g. looking for Home.py next to the repo instead of
# inside it).
ROOT = os.path.abspath(SPECPATH)

# PyInstaller doesn't bundle installed-package metadata (.dist-info) by
# default. Streamlit (and several of its own dependents, e.g. altair,
# pydeck) call importlib.metadata.version(...) on themselves at import time
# -- without this, `import streamlit` raises PackageNotFoundError inside the
# frozen exe even though the module itself is bundled fine.
_METADATA_PACKAGES = [
    "streamlit", "altair", "pydeck", "click", "packaging", "pandas",
    "gitpython", "protobuf", "tenacity", "toml", "tornado", "watchdog",
    "cachetools", "blinker", "requests", "pillow", "pyarrow",
]
metadata_datas = []
for _pkg in _METADATA_PACKAGES:
    try:
        metadata_datas += copy_metadata(_pkg)
    except Exception:
        pass  # optional dep not installed in this build env -- skip

# Streamlit's actual frontend (the compiled React SPA it serves at "/") ships
# as non-Python data files (streamlit/static/*) inside its own package.
# PyInstaller's import-graph analysis only follows .py code, so without this
# the frozen exe's server starts fine but every route 404s -- there's no
# index.html to serve.
streamlit_data = collect_data_files("streamlit")


def _tree(src_name: str):
    """(source_dir, dest_dir) pair copying a whole subfolder 1:1 into the bundle."""
    return (os.path.join(ROOT, src_name), src_name)


datas = [
    (os.path.join(ROOT, "Home.py"), "."),
    (os.path.join(ROOT, "VERSION"), "."),
    (os.path.join(ROOT, "extract_soa.py"), "."),
    (os.path.join(ROOT, "extract_rps.py"), "."),
    (os.path.join(ROOT, "reconcile.py"), "."),
    (os.path.join(ROOT, "parquet_tool.py"), "."),
    (os.path.join(ROOT, "pdf_tools.py"), "."),
    (os.path.join(ROOT, "PF.py"), "."),
    (os.path.join(ROOT, "statutory_extractor.py"), "."),
    (os.path.join(ROOT, "Combined_PF_Statutory.py"), "."),
    _tree("_pages"),
    _tree("tools"),
    _tree("redaction_tool"),
    _tree("je_audit_tool"),
    _tree("form26as_tool"),
] + metadata_datas + streamlit_data

hiddenimports = [
    "streamlit", "streamlit.web.cli", "streamlit.runtime.scriptrunner",
    "core.patterns", "core.profile_manager", "core.engine",
    "redactors.pdf_redactor", "redactors.word_redactor",
    "redactors.excel_redactor", "redactors.image_redactor",
    "utils.data_loader", "utils.column_mapper", "utils.risk_scoring",
    "utils.export_utils", "utils.doc_type_classifier", "utils.vendor_master",
    "utils.duckdb_helper",
    "tests.amount_tests", "tests.timing_tests", "tests.user_tests",
    "tests.vendor_tests", "tests.benford_test",
    "form26as", "form26as.loader", "form26as.parser", "form26as.merge",
    "form26as.summary", "form26as.writer", "form26as.cli",
    "pyarrow", "pyarrow.parquet", "duckdb", "pdfplumber", "fitz",
    "docx", "openpyxl", "xlsxwriter", "xlrd", "pytesseract", "PIL",
    "sklearn.ensemble", "sklearn.preprocessing", "scipy.stats",
    "plotly", "plotly.express", "plotly.graph_objects", "plotly.subplots",
    "bs4", "lxml", "python_calamine",
]

a = Analysis(
    ["launcher.py"],
    pathex=[
        ROOT,
        os.path.join(ROOT, "redaction_tool"),
        os.path.join(ROOT, "je_audit_tool"),
        os.path.join(ROOT, "form26as_tool"),
    ],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="Uzumaki",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    icon=None,
)
