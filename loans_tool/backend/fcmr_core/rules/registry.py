"""Rule registry and pipeline runner.

A rule is any callable with signature:
    rule(df: pl.DataFrame) -> pl.DataFrame

where the input frame has all canonical customer-master columns and the
output frame is the same frame with three columns appended per rule:
    _exc_{rule_id}_status   : "OK" | "WARN" | "ERROR"
    _exc_{rule_id}_code     : short exception code string or ""
    _exc_{rule_id}_desc     : human-readable description or ""

After all rules run, the reporting module collapses these into the final
wide and long CSVs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import polars as pl

RuleFn = Callable[[pl.DataFrame], pl.DataFrame]
ProgressFn = Callable[[int, int, str], None]  # (completed, total, rule_id)


@dataclass
class RuleMeta:
    rule_id: str
    description: str
    fn: RuleFn


_REGISTRY: list[RuleMeta] = []

CATEGORIES = [
    {
        "id": "kyc_format",
        "label": "KYC & Document Format",
        "rule_ids": [
            "pan_format",
            "aadhaar_format",
            "voter_id_format",
            "passport_format",
            "dl_format",
            "mobile_format",
            "email_format",
            "dob_validity",
            "dob_age_range",
            "bank_account_invalid_length",
            "email_company_generic_domain",
        ],
    },
    {
        "id": "address_pin",
        "label": "Address & PIN",
        "rule_ids": [
            "pincode_exists",
            "state_pin_match",
            "district_pin_match",
            "address_completeness",
        ],
    },
    {
        "id": "duplicates",
        "label": "Duplicate Detection",
        "rule_ids": [
            "pan_duplicate",
            "aadhaar_duplicate",
            "mobile_duplicate",
            "bank_account_duplicate",
            "name_dob_duplicate",
            "voter_id_duplicate",
            "address_duplicate",
        ],
    },
    {
        "id": "identity_grouping",
        "label": "Identity Grouping (UCID + Beneficiary)",
        "rule_ids": ["ucid", "beneficiary_tagging"],
    },
]


def register(rule_id: str, description: str) -> Callable[[RuleFn], RuleFn]:
    """Decorator to register a rule function."""

    def decorator(fn: RuleFn) -> RuleFn:
        _REGISTRY.append(RuleMeta(rule_id=rule_id, description=description, fn=fn))
        return fn

    return decorator


def list_rules() -> list[RuleMeta]:
    return list(_REGISTRY)


_NUMERIC_CANONICALS = {
    "loan_amount",
    "outstanding_principal",
    "emi_amount",
    "age",
    "sanctioned_amount",
    "disbursed_amount",
    "outstanding_balance",
}


def _coerce_str_columns(df: pl.DataFrame) -> pl.DataFrame:
    """Cast any non-numeric, non-exception column to Utf8.

    Polars scan_csv can infer Int64 for columns like bank_account or pincode
    when all values are numeric. Rules call .strip() on Python values iterated
    from those columns, which raises AttributeError on int. This coercion runs
    once before the rule pipeline so every rule sees string values.
    """
    casts = []
    for col in df.columns:
        if col.startswith("_exc_"):
            continue
        if col in _NUMERIC_CANONICALS:
            continue
        if df[col].dtype in (
            pl.Int8,
            pl.Int16,
            pl.Int32,
            pl.Int64,
            pl.UInt8,
            pl.UInt16,
            pl.UInt32,
            pl.UInt64,
            pl.Float32,
            pl.Float64,
        ):
            casts.append(pl.col(col).cast(pl.Utf8, strict=False))
    if casts:
        df = df.with_columns(casts)
    return df


def list_categories() -> list[dict]:
    """Return categories enriched with rule descriptions."""
    _ensure_rules_loaded()
    rule_map = {m.rule_id: m.description for m in _REGISTRY}
    result = []
    for cat in CATEGORIES:
        rules = []
        for rule_id in cat["rule_ids"]:
            rules.append({"id": rule_id, "description": rule_map.get(rule_id, "")})
        result.append({"id": cat["id"], "label": cat["label"], "rules": rules, "count": len(rules)})
    return result


def resolve_rule_ids(category_ids: list[str], rule_ids: list[str]) -> list[str] | None:
    """Resolve selected categories and rules into a unified rule_ids list.

    Returns:
        Merged list of rule IDs, or None if neither is provided (= run all).
    """
    if not category_ids and not rule_ids:
        return None

    selected = set()
    for cat_id in category_ids:
        for cat in CATEGORIES:
            if cat["id"] == cat_id:
                selected.update(cat["rule_ids"])
                break
    selected.update(rule_ids)
    return sorted(selected) if selected else None


def run_pipeline(
    df: pl.DataFrame,
    on_progress: ProgressFn | None = None,
    rule_ids: list[str] | None = None,
) -> pl.DataFrame:
    """Run registered rules in registration order, returning an annotated frame.

    Args:
        df: Input DataFrame.
        on_progress: Callback (completed, total, rule_id) after each rule.
        rule_ids: If provided, run only these rule IDs (preserving registry order).
                  If None, run all registered rules.

    Returns:
        Annotated frame with _exc_* columns appended per rule.
    """
    _ensure_rules_loaded()
    df = _coerce_str_columns(df)
    if rule_ids is None:
        selected_registry = _REGISTRY
    else:
        selected_set = set(rule_ids)
        selected_registry = [m for m in _REGISTRY if m.rule_id in selected_set]

    total = len(selected_registry)
    for idx, meta in enumerate(selected_registry):
        df = meta.fn(df)
        if on_progress:
            on_progress(idx + 1, total, meta.rule_id)
    return df


def _ensure_rules_loaded() -> None:
    if _REGISTRY:
        return
    # Import triggers registration via @register decorators
    from fcmr_core.rules import (
        bank_account,  # noqa: F401
        beneficiary,  # noqa: F401
        duplicates,  # noqa: F401
        email,  # noqa: F401
        kyc_format,  # noqa: F401
        pincode_address,  # noqa: F401
        ucid,  # noqa: F401
    )
