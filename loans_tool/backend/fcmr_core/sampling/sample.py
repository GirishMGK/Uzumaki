"""Deterministic stratified random sampling for audit workpapers.

Uses seeded random selection for reproducibility across re-runs.
Each sample is tagged with selection reason and criticality.
"""

from __future__ import annotations

import hashlib
import random
from pathlib import Path

import polars as pl

from fcmr_core.sampling.icai_table import get_sample_size
from fcmr_core.sampling.stratification import (
    get_exception_severity,
    stratify_by_exception_severity,
)


def _make_seed(engagement_id: str, run_id: str) -> int:
    """Create a deterministic seed from engagement and run IDs."""
    combined = f"{engagement_id}:{run_id}"
    hash_hex = hashlib.sha256(combined.encode()).hexdigest()
    return int(hash_hex[:8], 16)


def select_sample(
    wide_csv_path: Path,
    engagement_id: str,
    run_id: str,
    population: int,
    exception_count: int,
) -> list[dict]:
    """Deterministic stratified random sample selection.

    Args:
        wide_csv_path: Path to wide exception CSV.
        engagement_id: For seed reproducibility.
        run_id: For seed reproducibility.
        population: Total record count.
        exception_count: Records with exceptions.

    Returns:
        List of dicts: [{"row_index": i, "exception_codes": codes, "selection_reason": str, "criticality": str}]
    """
    if not wide_csv_path.exists():
        return []

    # Calculate sample size
    sample_size = get_sample_size(population, exception_count)
    if sample_size < 1:
        return []

    # Stratify records
    strata = stratify_by_exception_severity(wide_csv_path)

    # Set seed for reproducibility
    seed = _make_seed(engagement_id, run_id)
    random.seed(seed)

    # Read exception codes for each row
    try:
        df = pl.read_csv(wide_csv_path, columns=["exception_codes"], infer_schema_length=0)
        exception_codes_list = df["exception_codes"].to_list()
    except Exception:
        exception_codes_list = [""] * population

    # Proportional stratified sampling
    samples = []
    total_strata = sum(len(indices) for indices in strata.values())

    for stratum, indices in strata.items():
        if not indices:
            continue

        # Proportional allocation
        stratum_size = len(indices)
        stratum_proportion = stratum_size / total_strata if total_strata > 0 else 0
        stratum_sample_size = max(1, int(sample_size * stratum_proportion))
        stratum_sample_size = min(stratum_sample_size, stratum_size)

        # Stratified random selection
        selected_indices = random.sample(indices, stratum_sample_size)

        for row_idx in selected_indices:
            codes_str = (
                str(exception_codes_list[row_idx]) if row_idx < len(exception_codes_list) else ""
            )
            codes = [c.strip() for c in codes_str.split("|") if c.strip()] if codes_str else []

            # Find highest severity code
            max_severity = stratum
            if codes:
                for code in codes:
                    severity = get_exception_severity(code)
                    # Severity order: CRITICAL < HIGH < MEDIUM < LOW
                    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
                    if severity_order.get(severity, 3) < severity_order.get(max_severity, 3):
                        max_severity = severity

            # Selection reason
            if codes:
                selection_reason = f"{max_severity}: {codes[0]}"
            else:
                selection_reason = f"{max_severity}: No specific code"

            samples.append(
                {
                    "row_index": row_idx,
                    "exception_codes": codes_str,
                    "selection_reason": selection_reason,
                    "criticality": max_severity,
                }
            )

    # Sort by criticality then row index
    criticality_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "OK": 4}
    samples.sort(key=lambda x: (criticality_order.get(x["criticality"], 4), x["row_index"]))

    return samples[:sample_size]
