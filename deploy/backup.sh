#!/usr/bin/env bash
# Nightly backup of Goal Engine's database:  ./deploy/backup.sh
# - reads the login from .env (never printed, never on the command line)
# - writes /cdata/backups/goal-engine/goal_engine-YYYY-MM-DD_HHMM.sql.gz (only this user can read it)
# - keeps the last 7 days
set -euo pipefail
cd "$(dirname "$0")/.."

DEST="${BACKUP_DIR:-/cdata/backups/goal-engine}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-7}"
mkdir -p "$DEST"
chmod 700 "$DEST"

# the database login from .env, without printing it
get() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//'; }
DB_HOST="$(get DB_HOST)"; DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="$(get DB_PORT)"; DB_PORT="${DB_PORT:-3306}"
DB_NAME="$(get DB_NAME)"; DB_NAME="${DB_NAME:-goal_engine}"
DB_USER="$(get DB_USER)"; DB_USER="${DB_USER:-goal_engine}"
DB_PASSWORD="$(get DB_PASSWORD)"

# the password goes in a private temporary file, so it never shows in the process list
CNF="$(mktemp)"
trap 'rm -f "$CNF"' EXIT
chmod 600 "$CNF"
printf '[client]\nuser=%s\npassword=%s\nhost=%s\nport=%s\n' "$DB_USER" "$DB_PASSWORD" "$DB_HOST" "$DB_PORT" > "$CNF"

OUT="$DEST/${DB_NAME}-$(date +%F_%H%M).sql.gz"
umask 077
mysqldump --defaults-extra-file="$CNF" --single-transaction --quick --routines --triggers --no-tablespaces "$DB_NAME" | gzip -6 > "$OUT.part"
mv "$OUT.part" "$OUT"

# a backup that's too small is a failed backup: say so loudly
SIZE=$(stat -c %s "$OUT")
if [ "$SIZE" -lt 1000 ]; then
  echo "BACKUP-FAILED: $OUT is only $SIZE bytes" >&2
  exit 1
fi

find "$DEST" -name "${DB_NAME}-*.sql.gz" -mtime +"$KEEP_DAYS" -delete
echo "BACKUP-OK $OUT ($(du -h "$OUT" | cut -f1)), $(ls "$DEST"/${DB_NAME}-*.sql.gz | wc -l) backups kept"
