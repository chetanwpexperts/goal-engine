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
sleep 3

echo "== Health check"
curl -fsS localhost:8098/api/health && echo && echo "UPDATE-OK $(git log --oneline -1)"
