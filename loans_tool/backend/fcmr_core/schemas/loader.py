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

    def best_raw_for_canonical(self, raw_headers: list[str]) -> dict[str, str]:
        """Invert map_headers_with_scores() correctly -- see
        best_raw_for_canonical_from_scores() for why a plain inversion is
        wrong. Callers that already have a `scored` dict in hand (e.g. to
        show per-header confidence) should call
        best_raw_for_canonical_from_scores(scored) directly instead of this,
        to avoid running the fuzzy-match scoring pass (and its DB round
        trip for the threshold setting) a second time.
        """
        return best_raw_for_canonical_from_scores(self.map_headers_with_scores(raw_headers))

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


def best_raw_for_canonical_from_scores(
    scored: dict[str, tuple[str, float]],
) -> dict[str, str]:
    """Invert a {raw: (canonical, score)} map into {canonical: raw},
    keeping only the highest-scoring raw header when several headers all
    fuzzy-match the same canonical.

    A plain {canonical: raw for raw, (canonical, _) in scored.items()}
    inversion keeps whichever raw header happens to be seen *last*, not
    the best match -- so a near-duplicate column (e.g. a real file's own
    "_Hist"/"_Old" variant of an exact-match column) can silently steal a
    canonical's suggested-mapping slot from the correct exact match. The
    exact match then gets left unmapped (defaults to "Skip"), while the
    fuzzy variant gets suggested for renaming *into* that canonical's
    name -- and since the exact-match column is still sitting there under
    that same name, confirming that suggestion collides the two columns.
    """
    best: dict[str, tuple[str, float]] = {}
    for raw, (canonical, score) in scored.items():
        current = best.get(canonical)
        if current is None or score > current[1]:
            best[canonical] = (raw, score)
    return {canonical: raw for canonical, (raw, _score) in best.items()}


def resolve_column_renames(
    raw_columns: list[str], rename_map: dict[str, str]
) -> dict[str, str]:
    """Given a file's raw column names and an intended {raw: canonical}
    rename (however it was produced -- auto-suggested or hand-picked in
    the mapping UI), return a collision-free {raw: final_name} mapping
    covering *every* raw column, including ones rename_map doesn't touch.

    Naively applying rename_map as-is can silently collide two source
    columns onto the same output name -- e.g. a raw column that's
    already, coincidentally, named exactly like some *other* column's
    rename target. Renaming straight into that (polars' DataFrame.rename,
    or a duplicate SQL SELECT alias in the ingestion pipeline) either
    crashes outright or -- worse -- silently keeps only one side's data
    with no error at all.

    Resolution policy, chosen so nothing is ever silently dropped or
    overwritten:
    - An explicit entry in rename_map always gets the exact target name
      it asked for (first-registered wins if two different raw columns
      both target the same canonical -- itself an upstream data-quality
      issue, not something to silently resolve one specific way).
    - Every other raw column keeps its own name if that's still free,
      otherwise gets a numeric suffix ("name_2", "name_3", ...) so its
      data survives under a distinguishable, inspectable name instead of
      vanishing.
    """
    final_names: dict[str, str] = {}
    used: set[str] = set()

    def unique(base: str) -> str:
        if base not in used:
            return base
        i = 2
        while f"{base}_{i}" in used:
            i += 1
        return f"{base}_{i}"

    for raw in raw_columns:
        if raw in rename_map:
            target = unique(rename_map[raw])
            final_names[raw] = target
            used.add(target)

    for raw in raw_columns:
        if raw in final_names:
            continue
        name = unique(raw)
        final_names[raw] = name
        used.add(name)

    return final_names


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


# report_type -> display label, for the handful whose natural title-cased
# form ("Ead Files") reads wrong. Shared by every screen that lists report
# types (Analytics hub, Consolidate & Download, ...) so they can't drift
# from each other.
_LABEL_OVERRIDES = {"ead_files": "EAD Files"}


def label_for_report_type(report_type: str) -> str:
    return _LABEL_OVERRIDES.get(report_type, report_type.replace("_", " ").title())


def _reload() -> None:
    _REGISTRY.clear()
    for yaml_file in config_settings.schemas_dir.glob("*.yaml"):
        schema = _load_yaml(yaml_file)
        _REGISTRY[schema.report_type] = schema
