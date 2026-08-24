#!/usr/bin/env bash
# Backup the firm-rms database (§0.4 self-hosted, §11 audit_log retention >= 8
# years — the operational assumption docs/decisions.md names until this
# script existed). Two modes, auto-detected from RMS_DATABASE_URL:
#
#   Postgres (production, docker-compose): pg_dump in custom format via the
#   running `db` service, so it works identically whether this script runs
#   on the docker host or inside the `api` container.
#
#   SQLite (dev): a straight file copy — SQLite's on-disk format has no
#   separate "dump" step worth doing for a single-box dev database.
#
# Usage:
#   ./scripts/backup.sh [output-dir]        # defaults to ./backups
#
# Retention: keeps the last 30 daily backups in the output dir, deletes
# anything older (the firm's actual retention policy, per §11, is a
# storage/ops decision outside this script's scope — see docs/decisions.md).
set -euo pipefail

OUT_DIR="${1:-$(dirname "$0")/../backups}"
mkdir -p "$OUT_DIR"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"

# Load .env if present so RMS_DATABASE_URL is set the same way the app sees it.
ENV_FILE="$(dirname "$0")/../.env"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

DB_URL="${RMS_DATABASE_URL:-sqlite:///./firm_rms_dev.db}"

if [[ "$DB_URL" == sqlite* ]]; then
  DB_PATH="${DB_URL#sqlite:///}"
  DB_PATH="$(dirname "$0")/../${DB_PATH#./}"
  if [ ! -f "$DB_PATH" ]; then
    echo "No SQLite DB found at $DB_PATH — nothing to back up." >&2
    exit 1
  fi
  OUT_FILE="$OUT_DIR/firm_rms_${TIMESTAMP}.db"
  cp "$DB_PATH" "$OUT_FILE"
  echo "SQLite backup written: $OUT_FILE"
else
  # postgresql[+psycopg]://user:pass@host:port/dbname
  DB_URL_NO_DRIVER="${DB_URL#postgresql}"
  DB_URL_NO_DRIVER="postgresql${DB_URL_NO_DRIVER#+psycopg}"
  OUT_FILE="$OUT_DIR/firm_rms_${TIMESTAMP}.dump"
  pg_dump --format=custom --no-owner --no-privileges --dbname="$DB_URL_NO_DRIVER" --file="$OUT_FILE"
  echo "Postgres backup written: $OUT_FILE"
fi

# Retention: keep the last 30, prune older ones.
find "$OUT_DIR" -maxdepth 1 -type f \( -name "firm_rms_*.db" -o -name "firm_rms_*.dump" \) -mtime +30 -delete

echo "Verifying the backup is readable..."
if [[ "$OUT_FILE" == *.db ]]; then
  # python3's stdlib sqlite3 module rather than the `sqlite3` CLI — the CLI
  # isn't guaranteed to be installed everywhere this script runs, Python is
  # (it's the app's own runtime).
  python3 -c "
import sqlite3, sys
conn = sqlite3.connect('$OUT_FILE')
result = conn.execute('PRAGMA integrity_check;').fetchone()[0]
sys.exit(0 if result == 'ok' else 1)
" && echo "OK: SQLite integrity check passed."
else
  pg_restore --list "$OUT_FILE" > /dev/null && echo "OK: pg_restore can read the archive's table of contents."
fi
