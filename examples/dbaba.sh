#!/usr/bin/env bash
set -euo pipefail
base="${DBABA_URL:-http://127.0.0.1:8000}"
: "${DBABA_API_KEY:?Set DBABA_API_KEY in your environment}"
query="${1:-SELECT * FROM orders WHERE customer_id = 42;}"
jar=$(mktemp)
trap 'rm -f "$jar"' EXIT
curl --fail --silent --show-error -b "$jar" -c "$jar" \
  -H "Authorization: Bearer $DBABA_API_KEY" \
  -X POST "${base%/}/api/new-chat" >/dev/null
jq -n --arg text "$query" '{text: $text}' |
  curl --fail --silent --show-error -b "$jar" -c "$jar" \
    -H "Authorization: Bearer $DBABA_API_KEY" \
    -H 'Content-Type: application/json' \
    --data-binary @- "${base%/}/api/message" |
  jq -er '.reply'
