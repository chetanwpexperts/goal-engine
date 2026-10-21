#!/usr/bin/env bash
# Update the live app from GitHub:  ./deploy/update.sh
# Keeps .env and the database. Stops on the first error.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== Pulling latest code"
git pull --ff-only

echo "== Installing any new Python packages"
.venv/bin/pip install -q -r requirements.txt

echo "== Restarting"
sudo supervisorctl restart goal-engine

echo "== Health check (waits up to 60s while the ML model loads)"
for i in $(seq 1 30); do
  if curl -fsS localhost:8098/api/health 2>/dev/null; then
    echo && echo "UPDATE-OK $(git log --oneline -1)"
    exit 0
  fi
  sleep 2
done
echo "Not answering after 60s. Last errors:"
tail -20 logs/app.err.log
exit 1
