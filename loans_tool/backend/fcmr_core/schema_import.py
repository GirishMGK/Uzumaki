"""Bulk-import column-alias overrides from an Excel sheet shaped like the
NBFC's own master schema file (DataSet / Nature / Include / Source Column /
Output Name / Type / Nullable / Date Format) -- the same layout
fcmr_core/schemas/*.yaml was originally generated from (see
schemas/loader.py's _LABEL_OVERRIDES comment). Lets Settings teach the app
a new raw-header spelling (or, if neither the raw header nor its intended
output name is recognised at all, a brand-new canonical field) for an
existing report type, without a code change or app restart -- rows are
persisted via fcmr_core.catalog.store and picked up immediately by
schemas.loader.get_schema() on its next call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import openpyxl

from fcmr_core.schemas.loader import get_schema

# Nature (as the client's own master schema file names it) -> this app's
# report_type. Exact strings from that file; every current fcmr_core/schemas/*.yaml
# was generated from a sheet using these same Nature values.
NATURE_TO_REPORT_TYPE: dict[str, str] = {
    "customer master": "customer_master",
    "ead addl columns": "ead_addl_columns",
    "disb addl col.": "disbursement_addl_columns",
    "disb addl col": "disbursement_addl_columns",
    "collections": "collection_report",
    "closed loans": "closed_loans",
    "ead": "ead_files",
    "bank transfer": "bank_transfer",
    "cancelled/rejected": "cancelled_rejected",
    "cancelled rejected": "cancelled_rejected",
    "cibil report": "cibil_report",
    "emi due report": "emi_due_report",
    "sap addl columns": "sap_addl_columns",
    "disbursement": "disbursement_report",
}

_TYPE_TO_DTYPE: dict[str, str] = {
    "string": "str",
    "text": "str",
    "date": "str",  # no dedicated date dtype in this schema model -- see loader.py's ColumnSpec
    "double": "float",
    "float": "float",
    "decimal": "float",
    "number": "float",
    "int": "int",
    "integer": "int",
    "bigint": "int",
}

_FALSE_STRINGS = {"false", "0", "no", "n"}

_REQUIRED_HEADERS = ("nature", "source column", "output name")


@dataclass
class ImportRow:
    row_num: int
    nature: str
    report_type: str | None
    source_column: str
    output_name: str
    dtype: str
    required: bool
    status: str  # "unresolved_nature" | "already_known" | "new_alias" | "new_field"
    canonical: str | None = None  # resolved/would-be canonical key (None if unresolved)


class SchemaImportError(ValueError):
    """Raised when the uploaded workbook doesn't have the expected columns."""


def _norm(value: object) -> str:
    return re.sub(r"[^0-9a-z]+", "", str(value).lower()) if value is not None else ""


def _is_included(value: object) -> bool:
    if value is None:
        return True
    return str(value).strip().lower() not in _FALSE_STRINGS


def _is_required(nullable_value: object) -> bool:
    """Nullable=FALSE means the source marks this column mandatory."""
    return str(nullable_value).strip().lower() in _FALSE_STRINGS


def _resolve_report_type(nature: object) -> str | None:
    return NATURE_TO_REPORT_TYPE.get(str(nature).strip().lower())


def _slugify(name: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z]+", "_", name.strip()).strip("_").lower()
    return s or "field"


def parse_mapping_workbook(file: str | Path | BinaryIO) -> list[ImportRow]:
    """Parse the first sheet of an uploaded workbook into ImportRows,
    already classified against each resolved report_type's CURRENT
    effective schema (base YAML + any overrides already committed), so
    re-previewing or re-uploading the same sheet twice shows everything
    as already_known rather than offering to re-add it.
    """
    wb = openpyxl.load_workbook(file, data_only=True, read_only=True)
    ws = wb.worksheets[0]
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows_iter)
    except StopIteration:
        raise SchemaImportError("The sheet is empty.")

    header = [str(c).strip().lower() if c is not None else "" for c in header_row]
    col_idx = {h: i for i, h in enumerate(header)}
    missing = [h for h in _REQUIRED_HEADERS if h not in col_idx]
    if missing:
        raise SchemaImportError(
            f"Missing required column(s) in row 1: {', '.join(missing)}. "
            f"Expected at least: {', '.join(_REQUIRED_HEADERS)}."
        )

    def get(row: tuple, name: str) -> object:
        idx = col_idx.get(name)
        return row[idx] if idx is not None and idx < len(row) else None

    results: list[ImportRow] = []
    for row_num, row in enumerate(rows_iter, start=2):
        if not row or all(c is None for c in row):
            continue
        nature = get(row, "nature")
        source_column = get(row, "source column")
        output_name = get(row, "output name")
        if not nature or not source_column or not output_name:
            continue
        if not _is_included(get(row, "include")):
            continue

        type_raw = str(get(row, "type") or "").strip().lower()
        dtype = _TYPE_TO_DTYPE.get(type_raw, "str")
        required = _is_required(get(row, "nullable"))
        source_column = str(source_column).strip()
        output_name = str(output_name).strip()

        report_type = _resolve_report_type(nature)
        if report_type is None:
            results.append(
                ImportRow(row_num, str(nature), None, source_column, output_name, dtype, required, "unresolved_nature")
            )
            continue

        results.append(_classify_row(row_num, str(nature), report_type, source_column, output_name, dtype, required))

    return results


def _classify_row(
    row_num: int, nature: str, report_type: str, source_column: str, output_name: str, dtype: str, required: bool
) -> ImportRow:
    schema = get_schema(report_type)
    src_norm = _norm(source_column)
    out_norm = _norm(output_name)

    if schema:
        # The raw header itself is already recognised (as some canonical's
        # name or one of its aliases) -- nothing to teach it.
        for col in schema.columns:
            if _norm(col.canonical) == src_norm or any(_norm(a) == src_norm for a in col.aliases):
                return ImportRow(
                    row_num, nature, report_type, source_column, output_name, dtype, required, "already_known", col.canonical
                )
        # The raw header is new, but the field it should become already
        # exists under some other alias -- teach it this new spelling.
        for col in schema.columns:
            if _norm(col.canonical) == out_norm or any(_norm(a) == out_norm for a in col.aliases):
                return ImportRow(
                    row_num, nature, report_type, source_column, output_name, dtype, required, "new_alias", col.canonical
                )

    # Neither side is recognised -- a genuinely new canonical field.
    return ImportRow(
        row_num, nature, report_type, source_column, output_name, dtype, required, "new_field", _slugify(output_name)
    )
