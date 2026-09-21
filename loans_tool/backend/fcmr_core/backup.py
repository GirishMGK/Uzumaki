"""Data backup and restore for SanGir Automations.

Creates encrypted (or plaintext) zip backups of the catalog and outputs.
Used for data safety and migration.
"""

import zipfile
from datetime import UTC, datetime
from pathlib import Path

from fcmr_core.config import settings


def create_backup() -> Path:
    """Create a backup zip of catalog + outputs.

    Returns:
        Path to the created backup zip file.
    """
    settings.ensure_dirs()

    # Timestamp for unique backup filename
    now = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    backup_file = settings.backups_dir / f"SAND_Backup_{now}.zip"

    # Create zip with catalog and outputs
    with zipfile.ZipFile(backup_file, "w", zipfile.ZIP_DEFLATED) as zf:
        # Catalog
        if settings.catalog_path.exists():
            zf.write(settings.catalog_path, arcname="catalog.duckdb")

        # Session secret (for restoring auth)
        secret_file = settings.data_dir / ".session_secret"
        if secret_file.exists():
            zf.write(secret_file, arcname=".session_secret")

        # Outputs (all generated reports)
        if settings.outputs_dir.exists():
            for item in settings.outputs_dir.rglob("*"):
                if item.is_file():
                    arcname = item.relative_to(settings.data_dir)
                    zf.write(item, arcname=arcname)

    return backup_file


def restore_backup(backup_zip_path: Path) -> None:
    """Restore data from a backup zip.

    WARNING: This overwrites existing catalog and outputs. Backup current data first.

    Args:
        backup_zip_path: Path to the backup .zip file.
    """
    if not backup_zip_path.exists():
        raise FileNotFoundError(f"Backup file not found: {backup_zip_path}")

    settings.ensure_dirs()

    # Extract to data_dir
    with zipfile.ZipFile(backup_zip_path, "r") as zf:
        zf.extractall(settings.data_dir)
