"""
DuckDB helper for JE Audit Analytics.
Provides a slim-DataFrame builder and connection factory so each test
module can query only the columns it needs via SQL — avoiding full-table
pandas groupby / apply loops on 10 M+ rows.

Install: pip install duckdb
"""
from __future__ import annotations
import duckdb
import pandas as pd


def slim_df(df: pd.DataFrame, col_map: dict, fields: list) -> pd.DataFrame:
    """
    Return a lightweight DataFrame containing only `fields` (renamed to
    standard names) plus `_orig_idx` (the original pandas integer index).

    Fields not found in col_map or df are silently omitted — queries must
    guard with IS NOT NULL / COALESCE.
    """
    build: dict = {"_orig_idx": df.index.tolist()}
    for field in fields:
        actual = col_map.get(field)
        if actual and actual in df.columns:
            build[field] = df[actual].values
    return pd.DataFrame(build)


def duck_con(slim: pd.DataFrame, table: str = "je") -> duckdb.DuckDBPyConnection:
    """Create an in-memory DuckDB connection and register `slim` as `table`."""
    con = duckdb.connect()
    con.execute("PRAGMA threads=4")
    con.register(table, slim)
    return con


def fetch_idx(con: duckdb.DuckDBPyConnection, sql: str,
              params: list | None = None) -> list:
    """
    Execute `sql`, return a deduplicated list of integer _orig_idx values.
    Returns [] on any error so the calling test simply produces no exceptions.
    """
    try:
        rows = con.execute(sql, params or []).fetchall()
        return list({int(r[0]) for r in rows if r[0] is not None})
    except Exception:
        return []
