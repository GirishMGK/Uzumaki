"""PIN-code authentication and address validation rules.

Checks:
  1. PIN exists in the bundled India Post master (6-digit format + real code)
  2. Stated state matches the master for that PIN
  3. Stated district/city is consistent with the master
  4. Address completeness (line1, city, state, pincode all present)
"""

from __future__ import annotations

import re

import polars as pl

from fcmr_core.reference.pin_master import get_district_for_pin, get_state_for_pin, is_valid_pin
from fcmr_core.rules.registry import register

_PIN_RE = re.compile(r"^\d{6}$")


def _col_or_empty(df: pl.DataFrame, col: str) -> pl.Series:
    if col in df.columns:
        return df[col].cast(pl.Utf8, strict=False).fill_null("")
    return pl.Series(col, [""] * len(df), dtype=pl.Utf8)


def _annotate(
    df: pl.DataFrame, rule_id: str, statuses: list[str], codes: list[str], descs: list[str]
) -> pl.DataFrame:
    return df.with_columns(
        [
            pl.Series(f"_exc_{rule_id}_status", statuses, dtype=pl.Utf8),
            pl.Series(f"_exc_{rule_id}_code", codes, dtype=pl.Utf8),
            pl.Series(f"_exc_{rule_id}_desc", descs, dtype=pl.Utf8),
        ]
    )


@register(
    "pincode_exists", "PIN code existence: 6-digit format + validated against India Post master"
)
def rule_pincode_exists(df: pl.DataFrame) -> pl.DataFrame:
    pins = _col_or_empty(df, "pincode")
    statuses, codes, descs = [], [], []
    for pin in pins:
        pin = (pin or "").strip()
        if not pin:
            statuses.append("WARN")
            codes.append("PIN_MISSING")
            descs.append("Pincode not provided")
        elif not _PIN_RE.match(pin):
            statuses.append("ERROR")
            codes.append("PIN_INVALID_FORMAT")
            descs.append(f"Pincode '{pin}' is not a valid 6-digit number")
        elif not is_valid_pin(pin):
            statuses.append("ERROR")
            codes.append("PIN_NOT_FOUND")
            descs.append(f"Pincode '{pin}' not found in India Post master directory")
        else:
            statuses.append("OK")
            codes.append("")
            descs.append("")
    return _annotate(df, "pincode_exists", statuses, codes, descs)


@register(
    "state_pin_match",
    "State vs PIN: stated state must match India Post master for the PIN (if both provided)",
)
def rule_state_pin_match(df: pl.DataFrame) -> pl.DataFrame:
    pins = _col_or_empty(df, "pincode")
    states = _col_or_empty(df, "state")
    statuses, codes, descs = [], [], []
    for pin, stated_state in zip(pins, states):
        pin = (pin or "").strip()
        stated = (stated_state or "").strip().lower()
        if not pin or not stated:
            # Skip check if either value is missing (no flag â€” removed STATE_PIN_INCOMPLETE)
            statuses.append("OK")
            codes.append("")
            descs.append("")
            continue
        master_state = get_state_for_pin(pin)
        if master_state is None:
            # PIN unknown â€” already flagged by pincode_exists; skip double-reporting
            statuses.append("OK")
            codes.append("")
            descs.append("")
        elif master_state != stated:
            statuses.append("ERROR")
            codes.append("STATE_PIN_MISMATCH")
            descs.append(
                f"Stated state '{stated_state}' does not match India Post master "
                f"state '{master_state}' for PIN '{pin}'"
            )
        else:
            statuses.append("OK")
            codes.append("")
            descs.append("")
    return _annotate(df, "state_pin_match", statuses, codes, descs)


@register(
    "district_pin_match",
    "District vs PIN: stated district/city must be consistent with India Post master (if both provided)",
)
def rule_district_pin_match(df: pl.DataFrame) -> pl.DataFrame:
    pins = _col_or_empty(df, "pincode")
    districts = _col_or_empty(df, "district")
    cities = _col_or_empty(df, "city")
    statuses, codes, descs = [], [], []
    for pin, district, city in zip(pins, districts, cities):
        pin = (pin or "").strip()
        stated = (district or city or "").strip().lower()
        if not pin or not stated:
            # Skip check if either value is missing (no flag â€” removed DISTRICT_PIN_INCOMPLETE)
            statuses.append("OK")
            codes.append("")
            descs.append("")
            continue
        master_district = get_district_for_pin(pin)
        if master_district is None:
            statuses.append("OK")
            codes.append("")
            descs.append("")
        elif master_district not in stated and stated not in master_district:
            statuses.append("WARN")
            codes.append("DISTRICT_PIN_MISMATCH")
            descs.append(
                f"Stated district/city '{stated}' may not match India Post district "
                f"'{master_district}' for PIN '{pin}'"
            )
        else:
            statuses.append("OK")
            codes.append("")
            descs.append("")
    return _annotate(df, "district_pin_match", statuses, codes, descs)


@register(
    "address_completeness",
    "Address completeness: address_line1, city, state, and pincode must all be present",
)
def rule_address_completeness(df: pl.DataFrame) -> pl.DataFrame:
    required = ["address_line1", "city", "state", "pincode"]
    series_map = {col: _col_or_empty(df, col) for col in required}
    statuses, codes, descs = [], [], []
    for i in range(len(df)):
        missing = [col for col in required if not (series_map[col][i] or "").strip()]
        if missing:
            statuses.append("WARN")
            codes.append("ADDRESS_INCOMPLETE")
            descs.append(f"Address fields missing: {', '.join(missing)}")
        else:
            statuses.append("OK")
            codes.append("")
            descs.append("")
    return _annotate(df, "address_completeness", statuses, codes, descs)
