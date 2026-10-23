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

# the database login exactly as the app reads it (DATABASE_URL or the DB_* parts in .env), written straight into a
# private temporary file: never printed, never on a command line, never in the process list
CNF="$(mktemp)"
trap 'rm -f "$CNF"' EXIT
chmod 600 "$CNF"
DB_NAME="$(.venv/bin/python - "$CNF" <<'PY'
import sys
from sqlalchemy.engine import make_url
from app.config import get_settings

u = make_url(str(get_settings().sqlalchemy_url()) if not hasattr(get_settings().sqlalchemy_url(), "render_as_string")
             else get_settings().sqlalchemy_url().render_as_string(hide_password=False))
q = lambda v: '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'   # quoted: '#' or spaces in a password are kept
with open(sys.argv[1], "w") as f:
    f.write("[client]\n")
    f.write(f"user={q(u.username or '')}\n")
    f.write(f"password={q(u.password or '')}\n")
    f.write(f"host={u.host or '127.0.0.1'}\n")
    f.write(f"port={u.port or 3306}\n")
print(u.database or "goal_engine")
PY
)"

OUT="$DEST/${DB_NAME}-$(date +%F_%H%M).sql.gz"
umask 077
echo "Backing up database '$DB_NAME' as MySQL user '$(sed -n 's/^user="\(.*\)"$/\1/p' "$CNF")' on $(sed -n 's/^host=//p' "$CNF")"
# --defaults-file: ONLY this login, not ~/.my.cnf or /etc/mysql settings (they could name another user)
mysqldump --defaults-file="$CNF" --single-transaction --quick --routines --triggers --no-tablespaces "$DB_NAME" | gzip -6 > "$OUT.part"
mv "$OUT.part" "$OUT"

# a backup that's too small is a failed backup: say so loudly
SIZE=$(stat -c %s "$OUT")
if [ "$SIZE" -lt 1000 ]; then
  echo "BACKUP-FAILED: $OUT is only $SIZE bytes" >&2
  exit 1
fi

find "$DEST" -name "${DB_NAME}-*.sql.gz" -mtime +"$KEEP_DAYS" -delete
echo "BACKUP-OK $OUT ($(du -h "$OUT" | cut -f1)), $(ls "$DEST"/${DB_NAME}-*.sql.gz | wc -l) backups kept"
