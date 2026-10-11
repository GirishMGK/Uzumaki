"""JE Audit check 3 (High-Value Entries) flags against the data-driven
threshold app.py precomputes from the full dataset (`_hv_thresh` =
max(10 × median |amount|, 10% of performance materiality)), not against
performance materiality itself.
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pandas as pd
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JE_DIR = os.path.join(REPO_ROOT, "je_audit_tool")

HV_TYPE = "High-Value Entry (≥ Data-Driven Threshold)"


@pytest.fixture(scope="module")
def amount_tests():
    pytest.importorskip("duckdb")
    sys.path.insert(0, JE_DIR)  # for its `utils.*` imports
    try:
        # Loaded by path: its package is also named `tests`, like this repo's.
        spec = importlib.util.spec_from_file_location(
            "je_amount_tests", os.path.join(JE_DIR, "tests", "amount_tests.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(JE_DIR)


def _flagged(amount_tests, amounts, params):
    chunk = pd.DataFrame({"Amt": amounts})
    found = amount_tests.run_amount_rowlevel(chunk, {"amount": "Amt"}, params)
    return sorted(e["original_index"] for e in found if e["Exception Type"] == HV_TYPE)


def test_flags_at_the_precomputed_data_driven_threshold(amount_tests):
    params = {"performance_materiality": 1_000_000, "_hv_thresh": 50_000}
    # 60k is far below PM but above the data-driven threshold -> flagged.
    assert _flagged(amount_tests, [10_000, 60_000, 2_000_000], params) == [1, 2]


def test_falls_back_to_ten_percent_of_performance_materiality(amount_tests):
    params = {"performance_materiality": 1_000_000}  # no _hv_thresh
    assert _flagged(amount_tests, [10_000, 99_999, 100_000, 2_000_000], params) == [2, 3]


def test_negative_amounts_use_absolute_value(amount_tests):
    params = {"performance_materiality": 1_000_000, "_hv_thresh": 50_000}
    assert _flagged(amount_tests, [-60_000, 40_000], params) == [0]
