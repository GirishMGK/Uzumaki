import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)


def _isolate_loan_analytics_data() -> None:
    """Points the vendored Loan Analytics backend at a throwaway data dir for
    the whole test session. From source it otherwise uses the repo's
    loans_tool/backend/data/ -- the same catalog a developer's local runs
    use -- so tests leaked state into it, and each run's leftovers (mapped
    systems, alias overrides, uploads) broke the next run's assertions.

    Done here at conftest import, before any test imports loan_app: its
    loggers attach file handlers to logs_dir once per process."""
    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    try:
        from fcmr_core.config import settings
    except ImportError:
        return  # backend deps not installed -- its tests skip themselves
    finally:
        sys.path.remove(backend_dir)

    root = Path(tempfile.mkdtemp(prefix="uzumaki-tests-loans-"))
    settings.data_dir = root
    settings.uploads_dir = root / "uploads"
    settings.parquet_dir = root / "parquet"
    settings.outputs_dir = root / "outputs"
    settings.logs_dir = root / "logs"
    settings.backups_dir = root / "backups"
    settings.catalog_path = root / "catalog.duckdb"


_isolate_loan_analytics_data()
