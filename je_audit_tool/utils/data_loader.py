"""
Data Loader — handles single file, multiple files, and folder uploads.
All uploaded files are merged into a single DataFrame.
"""
from __future__ import annotations
import pandas as pd
import io
from typing import List, Tuple, Optional

# ── fast Excel engine ─────────────────────────────────────────────────────────
# python-calamine is ~5-10x faster than openpyxl for read-only loading.
# Fall back to openpyxl if not installed.
try:
    import python_calamine  # noqa: F401
    _XLSX_ENGINE = "calamine"
except ImportError:
    _XLSX_ENGINE = "openpyxl"


def _read_excel_fast(buf: bytes, sheet_name=0) -> pd.DataFrame:
    """Read Excel bytes using the fastest available engine."""
    return pd.read_excel(io.BytesIO(buf), sheet_name=sheet_name, engine=_XLSX_ENGINE)


def _read_csv_fast(buf: bytes) -> pd.DataFrame:
    """Read CSV bytes, trying utf-8-sig then latin-1."""
    try:
        return pd.read_csv(io.BytesIO(buf), low_memory=False, encoding="utf-8-sig", on_bad_lines="skip")
    except UnicodeDecodeError:
        return pd.read_csv(io.BytesIO(buf), low_memory=False, encoding="latin-1", on_bad_lines="skip")


def load_file_bytes(file_bytes: bytes, fname: str, sheet_name=0) -> pd.DataFrame:
    """
    Load a DataFrame from raw bytes.  This signature is cache-friendly:
    Streamlit's @st.cache_data can hash (file_bytes, fname, sheet_name) cheaply.
    """
    fname_lower = fname.lower()
    if fname_lower.endswith(".csv"):
        return _read_csv_fast(file_bytes)
    elif fname_lower.endswith((".xlsx", ".xls")):
        engine = _XLSX_ENGINE if fname_lower.endswith(".xlsx") else "xlrd"
        return pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet_name, engine=engine)
    else:
        raise ValueError(f"Unsupported file type: {fname}")


def load_single_file(file_obj) -> pd.DataFrame:
    """Load one uploaded Excel or CSV file object."""
    fname = getattr(file_obj, "name", "").lower()
    try:
        if fname.endswith(".csv"):
            return pd.read_csv(file_obj, low_memory=False, encoding="utf-8-sig", on_bad_lines="skip")
        elif fname.endswith(".xlsx"):
            return pd.read_excel(file_obj, engine=_XLSX_ENGINE)
        elif fname.endswith(".xls"):
            return pd.read_excel(file_obj, engine="xlrd")
        else:
            raise ValueError(f"Unsupported file type: {fname}")
    except UnicodeDecodeError:
        # Retry with latin-1 encoding for CSV
        if fname.endswith(".csv"):
            file_obj.seek(0)
            return pd.read_csv(file_obj, low_memory=False, encoding="latin-1", on_bad_lines="skip")
        raise


def load_multiple_files(uploaded_files) -> Tuple[pd.DataFrame, List[str]]:
    """
    Load and merge a list of uploaded file objects.
    Adds a '_source_file' column to track origin.
    Returns (merged_df, error_messages).
    """
    dfs: List[pd.DataFrame] = []
    errors: List[str] = []

    for f in uploaded_files:
        try:
            df = load_single_file(f)
            df["_source_file"] = f.name
            dfs.append(df)
        except Exception as exc:
            errors.append(f"{f.name}: {exc}")

    if not dfs:
        raise ValueError("No files could be loaded. Check file formats and try again.")

    merged = pd.concat(dfs, ignore_index=True)
    return merged, errors


def get_file_stats(df: pd.DataFrame, file_names: Optional[List[str]] = None) -> dict:
    """Return summary statistics about the loaded dataset."""
    return {
        "rows":       len(df),
        "columns":    len(df.columns),
        "memory_mb":  round(df.memory_usage(deep=True).sum() / 1024 / 1024, 2),
        "null_pct":   round(df.isnull().mean().mean() * 100, 1),
        "files":      len(file_names) if file_names else 1,
    }


def select_sheet(file_obj) -> Tuple[List[str], str]:
    """Return list of sheet names for Excel files."""
    fname = getattr(file_obj, "name", "").lower()
    if fname.endswith((".xlsx", ".xls")):
        xl = pd.ExcelFile(file_obj)
        return xl.sheet_names, xl.sheet_names[0]
    return [], ""


def select_sheet_from_bytes(file_bytes: bytes, fname: str) -> Tuple[List[str], str]:
    """Return list of sheet names for Excel bytes (cache-friendly variant)."""
    if fname.lower().endswith((".xlsx", ".xls")):
        engine = _XLSX_ENGINE if fname.lower().endswith(".xlsx") else "xlrd"
        xl = pd.ExcelFile(io.BytesIO(file_bytes), engine=engine)
        return xl.sheet_names, xl.sheet_names[0]
    return [], ""


def load_excel_sheet(file_obj, sheet_name: str) -> pd.DataFrame:
    """Load a specific sheet from an Excel file."""
    fname = getattr(file_obj, "name", "").lower()
    engine = _XLSX_ENGINE if fname.endswith(".xlsx") else "xlrd"
    return pd.read_excel(file_obj, sheet_name=sheet_name, engine=engine)
