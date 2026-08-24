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

from PyInstaller.utils.hooks import copy_metadata, collect_data_files, collect_submodules

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
    "setuptools", "platformdirs",
    # Firm RMS backend (FastAPI) -- same importlib.metadata.version() pattern
    # as streamlit above.
    "fastapi", "starlette", "uvicorn", "pydantic", "pydantic-settings",
    "sqlmodel", "sqlalchemy",
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

# Streamlit imports some of its own submodules dynamically at script-run
# time rather than via top-level `import` statements PyInstaller's static
# analysis can follow -- e.g. streamlit.runtime.scriptrunner.magic_funcs,
# needed for every script run (not just ones that visibly use "magic"
# commands), only surfaced once a real user ran the exe on Windows:
#   ModuleNotFoundError: No module named
#   'streamlit.runtime.scriptrunner.magic_funcs'
# collect_submodules("streamlit") (the whole package) was tried first but
# pulled in enough of streamlit's optional/back-compat surface to trigger an
# unrelated pkg_resources->platformdirs runtime-hook failure at startup, so
# scope this to just the scriptrunner package where the actual missing
# module lives.
streamlit_submodules = collect_submodules("streamlit.runtime.scriptrunner")

# passlib resolves its hash-scheme handlers (bcrypt, etc.) dynamically by
# name at runtime, same pattern as streamlit.runtime.scriptrunner above --
# collect the whole package rather than chase individual handler modules.
passlib_submodules = collect_submodules("passlib")
reportlab_submodules = collect_submodules("reportlab")
reportlab_data = collect_data_files("reportlab")  # bundled fonts/AFM metrics


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
    _tree("firm_rms_tool"),
] + metadata_datas + streamlit_data + reportlab_data

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
    "bs4", "lxml", "python_calamine", "pypdf",
    # setuptools' vendored pkg_resources ends up bundled transitively (via
    # streamlit's scriptrunner submodules) and PyInstaller's own runtime hook
    # for it (pyi_rth_pkgres.py) hard-requires platformdirs to be importable
    # -- without this the exe crashes on every launch before reaching
    # launcher.py at all, regardless of anything this app itself does.
    "platformdirs",
    # Firm RMS backend (FastAPI + uvicorn + SQLModel) -- resolves backends/
    # plugins via importlib rather than a plain top-level import, so
    # PyInstaller's static analysis can't discover them on its own. Carried
    # over from firm_rms_tool's own already-working desktop/firm_rms.spec
    # (see docs/user-guide.md#windows-desktop-app in the originating repo).
    "uvicorn.loops.auto", "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto", "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan.on", "uvicorn.lifespan.off",
    "passlib.handlers.bcrypt", "bcrypt", "email_validator",
    "apscheduler.triggers.cron", "apscheduler.triggers.interval",
    "apscheduler.executors.pool", "apscheduler.jobstores.memory",
    "jwt", "multipart", "sqlmodel", "pydantic", "pydantic_settings",
    "rapidfuzz",
] + streamlit_submodules + passlib_submodules + reportlab_submodules

# Three hub pages (_pages/pdf_tools_page.py, je_audit.py, pf_statutory.py)
# don't `import` their tool script -- they hand its filename to
# runpy.run_path() at runtime (_pages/_runner.py:run_script), because those
# legacy scripts call st.set_page_config() at module level and can only be
# entered that way. PyInstaller's static analysis walks literal `import`
# statements reachable from the entry script; a filename passed to
# runpy.run_path() is invisible to it, so *everything* pdf_tools.py,
# je_audit_tool/app.py, and Combined_PF_Statutory.py import -- their own
# top-level imports and every transitive one -- was missing from the bundle.
# (pypdf, used by tools/merger.py etc., was the first one a user actually
# hit; there was no reason more wouldn't follow.)
#
# Fix: list them as additional Analysis() scripts so PyInstaller traces their
# real import graphs too, then trim a.scripts back down to just launcher.py
# before EXE() -- their imports still land in the shared pyz/a.pure, but only
# launcher.py actually runs at startup.
# firm_rms_tool/backend/app/main.py is imported lazily (inside a function,
# not at _pages/firm_rms.py's module top level -- see _start_backend()) so
# it's *discoverable* by PyInstaller's AST-scanning static analysis either
# way, but its own third-party imports (fastapi, sqlmodel, ...) still need
# an entry point for that analysis to actually trace from -- same reasoning
# as the runpy-invisible scripts above, applied to a plain lazy import.
a = Analysis(
    ["launcher.py", "pdf_tools.py",
     os.path.join("je_audit_tool", "app.py"), "Combined_PF_Statutory.py",
     os.path.join("firm_rms_tool", "backend", "app", "main.py")],
    pathex=[
        ROOT,
        os.path.join(ROOT, "redaction_tool"),
        os.path.join(ROOT, "je_audit_tool"),
        os.path.join(ROOT, "form26as_tool"),
        os.path.join(ROOT, "firm_rms_tool", "backend"),
    ],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
a.scripts = [s for s in a.scripts if s[0] == "launcher"]

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
