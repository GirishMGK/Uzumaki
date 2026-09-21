import os
import secrets
import sys
from pathlib import Path

import psutil
from pydantic_settings import BaseSettings, SettingsConfigDict


def _detect_tier() -> str:
    """Detect hardware tier from total system RAM.

    low  (<12 GB)  — budget laptops
    mid  (12–24 GB) — standard business laptops
    high (>24 GB)  — workstations
    """
    try:
        gb = psutil.virtual_memory().total / 1024**3
    except Exception:
        gb = 8  # safe fallback
    if gb < 12:
        return "low"
    if gb < 24:
        return "mid"
    return "high"


# DuckDB limits per tier — keeps DuckDB from consuming all system RAM.
# memory_limit caps in-process usage; spill to temp_dir when over limit.
_DUCK_LIMITS = {
    "low": {"memory_gb": 3, "threads": 2},
    "mid": {"memory_gb": 6, "threads": 4},
    "high": {"memory_gb": 12, "threads": 6},
}

# On Vercel the filesystem is read-only except /tmp
_ON_VERCEL = bool(os.environ.get("VERCEL"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FCMR_", env_file=".env", extra="ignore")

    # Root paths — frozen-aware (PyInstaller bundles are read-only; use per-user dir)
    base_dir: Path = Path(__file__).resolve().parent.parent
    data_dir: Path = ""  # Set in __init__
    uploads_dir: Path = ""  # Set in __init__
    parquet_dir: Path = ""  # Set in __init__
    outputs_dir: Path = ""  # Set in __init__
    logs_dir: Path = ""  # Set in __init__
    backups_dir: Path = ""  # Set in __init__
    reference_dir: Path = Path(__file__).resolve().parent / "reference"
    schemas_dir: Path = Path(__file__).resolve().parent / "schemas"
    catalog_path: Path = ""  # Set in __init__

    # Backend and networking
    backend_port: int = 8000

    # Hardware tier — auto-detected; override with FCMR_HW_TIER=low|mid|high
    hw_tier: str = ""

    # DuckDB resource limits — derived from tier; override individually if needed
    duckdb_memory_limit: str = ""  # e.g. "6GB"; empty = auto-detect from tier
    duckdb_threads: int = 0  # 0 = auto-detect from tier

    # Ingest tuning — keep chunk size low enough to stay inside 15 GB RAM when
    # processing 5M-row CSVs with many wide columns.
    ingest_chunk_rows: int = 100_000
    max_upload_bytes: int = 2 * 1024**3  # 2 GB hard limit per upload

    # Column mapping — fuzzy match threshold (0.0–1.0; suggest if >= this score)
    fuzzy_match_threshold: float = 0.6

    # Aadhaar — salt is environment-injectable; do NOT hardcode a real value here.
    aadhaar_hash_salt: str = "change-me-via-FCMR_AADHAAR_HASH_SALT"

    # Session secret for signed cookies; persisted in data/ so sessions survive restarts
    session_secret: str = ""

    def __init__(self, **data):
        super().__init__(**data)

        # Resolve data_dir based on deployment environment
        if _ON_VERCEL:
            # On Vercel the filesystem is read-only except /tmp
            data_root = Path("/tmp/fcmr")
        elif getattr(sys, "frozen", False):
            # Running as PyInstaller bundle — use per-user appdata path
            if sys.platform == "win32":
                local_appdata = os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local")
                data_root = Path(local_appdata) / "SanGirAutomations"
            else:
                data_root = Path.home() / ".sangir"
        else:
            # Dev mode — use repo-relative data/ directory
            data_root = self.base_dir / "data"

        # Resolve hardware tier and DuckDB limits
        if not self.hw_tier:
            self.hw_tier = _detect_tier()
        limits = _DUCK_LIMITS.get(self.hw_tier, _DUCK_LIMITS["low"])
        if not self.duckdb_memory_limit:
            self.duckdb_memory_limit = f"{limits['memory_gb']}GB"
        if not self.duckdb_threads:
            self.duckdb_threads = limits["threads"]

        self.data_dir = data_root
        self.uploads_dir = self.data_dir / "uploads"
        self.parquet_dir = self.data_dir / "parquet"
        self.outputs_dir = self.data_dir / "outputs"
        self.logs_dir = self.data_dir / "logs"
        self.backups_dir = self.data_dir / "backups"
        self.catalog_path = self.data_dir / "catalog.duckdb"

        # If session_secret is not provided, load from or generate one in data/
        if not self.session_secret:
            if _ON_VERCEL:
                # On Vercel each container is stateless — secret MUST be fixed via env var
                # so session cookies are valid across all containers.
                # Set FCMR_SESSION_SECRET in Vercel project environment variables.
                raise ValueError(
                    "FCMR_SESSION_SECRET environment variable must be set on Vercel. "
                    "Go to Vercel → Project → Settings → Environment Variables and add "
                    "FCMR_SESSION_SECRET with a long random string."
                )
            else:
                secret_file = self.data_dir / ".session_secret"
                if secret_file.exists():
                    self.session_secret = secret_file.read_text().strip()
                else:
                    self.session_secret = secrets.token_urlsafe(32)
                    self.data_dir.mkdir(parents=True, exist_ok=True)
                    secret_file.write_text(self.session_secret)

    def ensure_dirs(self) -> None:
        dirs = (
            self.uploads_dir,
            self.parquet_dir,
            self.outputs_dir,
            self.logs_dir,
            self.backups_dir,
        )
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()


def apply_duckdb_limits(con) -> None:
    """Apply memory and thread limits to a DuckDB connection.

    Call this once immediately after opening any DuckDB connection that will
    run analytics (duplicate detection, UCID grouping, etc.).  The catalog
    connection (metadata only) does not need these limits — they are already
    conservative by default — but it doesn't hurt to apply them there too.
    """
    spill_dir = str(settings.data_dir / "duckdb_spill")
    try:
        con.execute(f"SET memory_limit='{settings.duckdb_memory_limit}'")
        con.execute(f"SET threads={settings.duckdb_threads}")
        con.execute(f"SET temp_directory='{spill_dir}'")
    except Exception:
        pass  # Older DuckDB version or in-memory DB — limits are advisory
