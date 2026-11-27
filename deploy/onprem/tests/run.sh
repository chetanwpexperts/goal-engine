#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/../../.."
targets=(ubuntu:22.04 ubuntu:24.04 debian:12 almalinux:9 docker)
if (($#)); then targets=("$@"); fi
for target in "${targets[@]}"; do
  case "$target" in ubuntu:22.04|ubuntu:24.04|debian:12|almalinux:9|docker) ;; *) echo "Unknown target: $target" >&2; exit 2;; esac
done
command -v docker >/dev/null && docker info >/dev/null 2>&1 || { echo "Docker daemon required." >&2; exit 2; }
results=$(mktemp)
trap 'rm -f "$results"' EXIT
status=0
for target in "${targets[@]}"; do
  if ! bash deploy/onprem/tests/target.sh "$target" "$results"; then status=1; fi
done
{
  echo '| Target | Step | Result |'
  echo '| --- | --- | --- |'
  cat "$results"
} | tee "${DBABA_TEST_TABLE:-/dev/null}"
exit "$status"
