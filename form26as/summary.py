"""Aggregate flat transactions into rollup summaries for quick analysis."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from .parser import Transaction, _to_date

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@dataclass
class SummaryRow:
    key: Tuple[str, ...]
    count: int = 0
    amount_paid: float = 0.0
    tax_deducted: float = 0.0
    tds_deposited: float = 0.0


def _aggregate(
    transactions: List[Transaction],
    key_fn: Callable[[Transaction], Tuple[str, ...]],
) -> List[SummaryRow]:
    buckets: "OrderedDict[Tuple[str, ...], SummaryRow]" = OrderedDict()
    for txn in transactions:
        key = key_fn(txn)
        row = buckets.get(key)
        if row is None:
            row = SummaryRow(key=key)
            buckets[key] = row
        row.count += 1
        row.amount_paid += txn.amount_paid or 0.0
        row.tax_deducted += txn.tax_deducted or 0.0
        row.tds_deposited += txn.tds_deposited or 0.0
    return list(buckets.values())


def by_deductor(transactions: List[Transaction]) -> List[SummaryRow]:
    rows = _aggregate(transactions, lambda t: (t.name_of_deductor, t.tan_of_deductor))
    rows.sort(key=lambda r: r.tds_deposited, reverse=True)
    return rows


def by_section(transactions: List[Transaction]) -> List[SummaryRow]:
    rows = _aggregate(transactions, lambda t: (t.section,))
    rows.sort(key=lambda r: r.tds_deposited, reverse=True)
    return rows


def _month_key(txn: Transaction) -> Tuple[str, ...]:
    d = _to_date(txn.transaction_date)
    if d is None:
        return ("Unknown",)
    return (f"{d.year}-{d.month:02d} {_MONTHS[d.month - 1]}",)


def by_month(transactions: List[Transaction]) -> List[SummaryRow]:
    rows = _aggregate(transactions, _month_key)
    rows.sort(key=lambda r: r.key[0])
    return rows


def totals(transactions: List[Transaction]) -> SummaryRow:
    row = SummaryRow(key=("Total",))
    for txn in transactions:
        row.count += 1
        row.amount_paid += txn.amount_paid or 0.0
        row.tax_deducted += txn.tax_deducted or 0.0
        row.tds_deposited += txn.tds_deposited or 0.0
    return row
