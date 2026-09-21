"""Load and apply column-mapping YAMLs to normalise uploaded CSV headers."""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from fcmr_core.catalog import store as catalog_store
from fcmr_core.config import settings as config_settings

_REGISTRY: dict[str, SchemaMap] = {}


@dataclass
class ColumnSpec:
    canonical: str
    aliases: list[str]
    required: bool
    dtype: str


@dataclass
class SchemaMap:
    report_type: str
    columns: list[ColumnSpec]
    # alias (lower) -> canonical
    _index: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        for col in self.columns:
            for alias in col.aliases:
                self._index[alias.lower().strip()] = col.canonical

    def map_headers(self, raw_headers: list[str]) -> dict[str, str]:
        """Return {raw_header: canonical_name} for every recognised column."""
        mapping: dict[str, str] = {}
        for h in raw_headers:
            canonical = self._index.get(h.lower().strip())
            if canonical:
                mapping[h] = canonical
        return mapping

    def score_header_match(self, raw_header: str, canonical: str) -> float:
        """Score a raw header against a canonical field's aliases. Returns [0.0, 1.0]."""
        for col in self.columns:
            if col.canonical == canonical:
                raw_lower = raw_header.lower().strip()
                best_score = 0.0
                for alias in col.aliases:
                    alias_lower = alias.lower().strip()
                    # Exact match = 1.0
                    if raw_lower == alias_lower:
                        return 1.0
                    # Use SequenceMatcher for fuzzy matching
                    score = difflib.SequenceMatcher(None, raw_lower, alias_lower).ratio()
                    best_score = max(best_score, score)
                return best_score
        return 0.0

    def map_headers_with_scores(self, raw_headers: list[str]) -> dict[str, tuple[str, float]]:
        """Return {raw_header: (canonical_name, confidence_score)} for all recognisable columns."""
        # Load threshold from database (or config default)
        threshold_str = catalog_store.get_setting("fuzzy_match_threshold")
        try:
            threshold = (
                float(threshold_str) if threshold_str else config_settings.fuzzy_match_threshold
            )
        except (ValueError, TypeError):
            threshold = config_settings.fuzzy_match_threshold

        result: dict[str, tuple[str, float]] = {}
        for h in raw_headers:
            best_match = None
            best_score = 0.0
            # Score against all canonicals
            for col in self.columns:
                score = self.score_header_match(h, col.canonical)
                if score > best_score:
                    best_score = score
                    best_match = col.canonical
            # Only include if score meets threshold
            if best_match and best_score >= threshold:
                result[h] = (best_match, round(best_score, 2))
        return result

    def missing_required(self, mapped: dict[str, str]) -> list[str]:
        found_canonicals = set(mapped.values())
        return [
            c.canonical for c in self.columns if c.required and c.canonical not in found_canonicals
        ]

    def dtype_for(self, canonical: str) -> str:
        for col in self.columns:
            if col.canonical == canonical:
                return col.dtype
        return "str"


def _load_yaml(path: Path) -> SchemaMap:
    with path.open("r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)
    cols = []
    for canonical, spec in raw.get("columns", {}).items():
        cols.append(
            ColumnSpec(
                canonical=canonical,
                aliases=spec.get("aliases", [canonical]),
                required=spec.get("required", False),
                dtype=spec.get("dtype", "str"),
            )
        )
    return SchemaMap(report_type=raw["report_type"], columns=cols)


def get_schema(report_type: str) -> SchemaMap | None:
    if not _REGISTRY:
        _reload()
    return _REGISTRY.get(report_type)


def available_report_types() -> list[str]:
    if not _REGISTRY:
        _reload()
    return sorted(_REGISTRY.keys())


def get_canonical_fields(report_type: str) -> list[ColumnSpec]:
    """Return all canonical column specs for a report type, required ones first."""
    schema = get_schema(report_type)
    if not schema:
        return []
    return sorted(schema.columns, key=lambda c: (not c.required, c.canonical))


def _reload() -> None:
    _REGISTRY.clear()
    for yaml_file in config_settings.schemas_dir.glob("*.yaml"):
        schema = _load_yaml(yaml_file)
        _REGISTRY[schema.report_type] = schema
