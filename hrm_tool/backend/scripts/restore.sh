#!/usr/bin/env bash
# Restore firm-rms from a backup produced by backup.sh. Mirrors backup.sh's
# Postgres/SQLite auto-detection.
#
# Usage:
#   ./scripts/restore.sh <backup-file> [--yes]
#
# Postgres: restores into the database named in RMS_DATABASE_URL, dropping
# and recreating existing objects first (--clean --if-exists) — this
# overwrites whatever is currently in that database, hence the confirmation
# prompt (skip it with --yes for a scripted/CI drill).
#
# SQLite: copies the backup file over the configured DB path, after moving
# the existing file aside with a .bak-<timestamp> suffix rather than
# deleting it outright (§0.1 "nothing is ever hard-deleted" — the same
# posture extends to this script even though it's outside the app itself).
set -euo pipefail

BACKUP_FILE="${1:?Usage: restore.sh <backup-file> [--yes]}"
ASSUME_YES="${2:-}"

if [ ! -f "$BACKUP_FILE" ]; then
  echo "Backup file not found: $BACKUP_FILE" >&2
  exit 1
fi

ENV_FILE="$(dirname "$0")/../.env"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

DB_URL="${RMS_DATABASE_URL:-sqlite:///./firm_rms_dev.db}"

confirm() {
  if [ "$ASSUME_YES" = "--yes" ]; then
    return 0
  fi
  read -r -p "This will overwrite the current database with $BACKUP_FILE. Continue? [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]]
}

if [[ "$DB_URL" == sqlite* ]]; then
  DB_PATH="${DB_URL#sqlite:///}"
  DB_PATH="$(dirname "$0")/../${DB_PATH#./}"
  confirm || { echo "Aborted."; exit 1; }
  if [ -f "$DB_PATH" ]; then
    mv "$DB_PATH" "${DB_PATH}.bak-$(date -u +%Y%m%dT%H%M%SZ)"
  fi
  cp "$BACKUP_FILE" "$DB_PATH"
  echo "Restored SQLite DB to $DB_PATH"
else
  DB_URL_NO_DRIVER="${DB_URL#postgresql}"
  DB_URL_NO_DRIVER="postgresql${DB_URL_NO_DRIVER#+psycopg}"
  confirm || { echo "Aborted."; exit 1; }
  pg_restore --clean --if-exists --no-owner --no-privileges --dbname="$DB_URL_NO_DRIVER" "$BACKUP_FILE"
  echo "Restored Postgres database from $BACKUP_FILE"
fi
