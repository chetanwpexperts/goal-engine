#!/usr/bin/env bash
# Nightly on the server (cron, 1:30 AM): start the capped model and engines, learn for 3.5 hours, stop them again,
# and if new rules were proved, run the rule tests and push them. Nothing reaches users until a normal deploy.
set -uo pipefail
cd "$(dirname "$0")/../.."
C="docker compose -f tools/knowledge/docker-compose.yml --profile server"
$C up -d && sleep 40
docker exec knowledge-ollama-1 ollama pull "${HARVEST_MODEL:-qwen2.5-coder:7b}" >/dev/null
before=$(md5sum app/knowledge/sql_rules/learned.json | cut -d' ' -f1)
nice -n 15 .kvenv/bin/python tools/knowledge/harvest.py --hours 3.5
$C down
after=$(md5sum app/knowledge/sql_rules/learned.json | cut -d' ' -f1)
if [ "$before" != "$after" ]; then
  if .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_learned_rules.py >/dev/null 2>&1; then
    git add app/knowledge/sql_rules/learned.json && git commit -q -m "Learned SQL rules (nightly, server)" && git push -q \
      && echo "pushed new rules" || echo "rules saved here; push failed (see git output)"
  else
    git checkout -- app/knowledge/sql_rules/learned.json && echo "a new rule failed the safety test: tonight's rules dropped"
  fi
fi
