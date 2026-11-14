#!/usr/bin/env bash
# Start the lab databases and wait until each one answers (at most 10 minutes). LAB_PW must be set.
set -euo pipefail
cd "$(dirname "$0")"
bash ./guard.sh
: "${LAB_PW:?set LAB_PW}"
docker compose -p dbaba-lab up -d
for i in $(seq 1 120); do
  if python3 smoke.py --quiet; then echo "LAB-UP"; exit 0; fi
  sleep 5
done
echo "lab databases did not come up in 10 minutes" >&2
docker compose ps >&2
exit 1
