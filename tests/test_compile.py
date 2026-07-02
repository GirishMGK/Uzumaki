"""
Repo-wide compile sweep.

This is the cheapest possible regression test, but it is exactly what would
have caught the pdf_tools.py stub-file mixup immediately: a syntactically
valid 162-line stub compiled fine, so only an *execution*-level check catches
that class of bug. Combined with test_regressions.py's dispatch check, this
pair replaces the manual verification steps done for each fix in this repo's
history.
"""
from __future__ import annotations

import py_compile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

EXCLUDE_DIRS = {".git", "__pycache__", "je_audit_tool_blazor"}


def _all_python_files():
    for path in REPO_ROOT.rglob("*.py"):
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        yield path


def test_all_python_files_compile():
    failures = []
    for path in _all_python_files():
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            failures.append(f"{path.relative_to(REPO_ROOT)}: {exc}")
    assert not failures, "Compile errors:\n" + "\n".join(failures)
