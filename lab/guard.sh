#!/usr/bin/env bash
# Refuse to touch Docker unless it is the lab user's own rootless Docker (docs/lab-server.md). The server's main Docker can
# see and stop every container on the machine, including other people's; the rootless one only sees the lab's own.
set -euo pipefail
if [ "$(id -u)" = "0" ]; then echo "refusing: the lab never runs as root" >&2; exit 1; fi
case "${DOCKER_HOST:-}" in
  unix:///run/user/*/docker.sock) ;;
  *) echo "refusing: DOCKER_HOST is not the lab user's rootless Docker (got '${DOCKER_HOST:-unset}')" >&2; exit 1 ;;
esac
if ! docker info --format '{{json .SecurityOptions}}' 2>/dev/null | grep -q rootless; then
  echo "refusing: this Docker is not rootless" >&2; exit 1
fi
