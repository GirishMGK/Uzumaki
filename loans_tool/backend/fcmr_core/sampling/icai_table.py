"""ICAI-ICFR attribute sampling table for audit sample size determination.

Reference: ICAI Guidance on Audit Sampling (Appendix A)
Confidence Level: 95% (z-score ≈ 1.96)
Reliability factor for attribute sampling
"""

from __future__ import annotations

# ICAI-ICFR attribute sampling table
# Key: (population_size_range, expected_deviation_rate) -> sample_size
# Simplified table for 95% confidence level

_ICAI_TABLE = {
    # (pop_min, pop_max, exp_dev_min, exp_dev_max) -> sample_size
    # Population up to 250, 5% expected deviation
    ((0, 250), (0.00, 0.05)): 47,
    ((0, 250), (0.05, 0.10)): 30,
    ((0, 250), (0.10, 0.15)): 20,
    ((0, 250), (0.15, 0.20)): 15,
    # Population 251–500, 5% expected deviation
    ((251, 500), (0.00, 0.05)): 47,
    ((251, 500), (0.05, 0.10)): 30,
    ((251, 500), (0.10, 0.15)): 20,
    ((251, 500), (0.15, 0.20)): 15,
    # Population 501–1000
    ((501, 1000), (0.00, 0.05)): 47,
    ((501, 1000), (0.05, 0.10)): 30,
    ((501, 1000), (0.10, 0.15)): 20,
    ((501, 1000), (0.15, 0.20)): 15,
    # Population 1001–5000
    ((1001, 5000), (0.00, 0.05)): 76,
    ((1001, 5000), (0.05, 0.10)): 52,
    ((1001, 5000), (0.10, 0.15)): 34,
    ((1001, 5000), (0.15, 0.20)): 25,
    # Population 5001–50000
    ((5001, 50000), (0.00, 0.05)): 160,
    ((5001, 50000), (0.05, 0.10)): 100,
    ((5001, 50000), (0.10, 0.15)): 65,
    ((5001, 50000), (0.15, 0.20)): 50,
    # Population 50001–500000
    ((50001, 500000), (0.00, 0.05)): 195,
    ((50001, 500000), (0.05, 0.10)): 125,
    ((50001, 500000), (0.10, 0.15)): 80,
    ((50001, 500000), (0.15, 0.20)): 60,
    # Population > 500000
    ((500001, float("inf")), (0.00, 0.05)): 195,
    ((500001, float("inf")), (0.05, 0.10)): 125,
    ((500001, float("inf")), (0.10, 0.15)): 80,
    ((500001, float("inf")), (0.15, 0.20)): 60,
}


def get_sample_size(
    population: int,
    exception_count: int,
    tolerable_deviation: float = 0.05,
) -> int:
    """Get sample size from ICAI-ICFR table.

    Args:
        population: Total number of records.
        exception_count: Number of records with exceptions.
        tolerable_deviation: Acceptable error rate (default 5%).

    Returns:
        Sample size from ICAI table. Minimum 20, maximum population.
    """
    # Calculate expected deviation rate
    expected_deviation = exception_count / population if population > 0 else 0

    # Find matching entry in table
    for ((pop_min, pop_max), (dev_min, dev_max)), sample_size in _ICAI_TABLE.items():
        if pop_min <= population <= pop_max and dev_min <= expected_deviation < dev_max:
            return min(sample_size, population)

    # Fallback: conservative estimate (sqrt of population)
    return min(max(20, int(population**0.5)), population)
