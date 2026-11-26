#!/usr/bin/env bash
# Run on a disposable Linux Docker host with systemd-compatible cgroups.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)
command -v docker >/dev/null || { echo "Please install Docker to run this integration test."; exit 1; }
docker info >/dev/null
tag="dbaba-onprem-test:$$"
container="dbaba-onprem-test-$$"
cleanup() {
  if docker inspect "$container" >/dev/null 2>&1; then
    docker rm -f "$container" >/dev/null
  fi
  docker image rm "$tag" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker build -t "$tag" - <<'DOCKERFILE'
FROM ubuntu:24.04
ENV container=docker
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y \
    systemd systemd-sysv python3 python3-venv curl ca-certificates iproute2 \
    && rm -rf /var/lib/apt/lists/*
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
DOCKERFILE

# Use a real init process and private cgroup namespace, rather than mocking
# systemctl. No service port is published to the Docker host.
docker run -d --privileged --cgroupns=private --tmpfs /run --tmpfs /run/lock \
  --name "$container" -v "$repo:/src:ro" "$tag" >/dev/null
ready=0
for ((i=0; i<30; i++)); do
  if docker exec "$container" systemctl show-environment >/dev/null 2>&1; then
    ready=1; break
  fi
  sleep 1
done
(( ready )) || { docker logs "$container"; echo "Please use a Docker host that supports systemd containers."; exit 1; }

run() { docker exec "$container" "$@"; }
health() {
  run systemctl is-active --quiet dbaba.service
  run systemctl is-enabled --quiet dbaba.service
  run bash -ec 'curl --noproxy "*" -fsS http://127.0.0.1:8765/api/health | python3 -c "import json,sys; assert json.load(sys.stdin)[\"ok\"] is True"'
  run bash -ec 'ss -H -ltn "sport = :8765" | awk '\''{ if ($4 != "127.0.0.1:8765") exit 1; count++ } END { if (!count) exit 1 }'\'''
}

# Synthetic screen password for a disposable container; no database setup.
printf '8765\nintegration-test-screen\nintegration-test-screen\n0\n' |
  docker exec -i -w /src -e DBABA_SRC=. "$container" bash deploy/onprem/install.sh
health
run python3 -c '
import hashlib, json, pathlib, stat
p = pathlib.Path("/opt/dbaba/dbaba.json")
c = json.loads(p.read_text())
assert stat.S_IMODE(p.stat().st_mode) == 0o600
assert c["version"] == 1 and c["port"] == 8765
assert c["database"] is None and c["api_keys"] == []
assert c["updates"] == {"enabled": True} and c["alerts"] == {"email": None}
s = c["screen_password"]
assert s["algorithm"] == "scrypt" and (s["n"], s["r"], s["p"]) == (16384, 8, 1)
assert hashlib.scrypt(b"integration-test-screen", salt=bytes.fromhex(s["salt"]),
                      n=s["n"], r=s["r"], p=s["p"], dklen=len(bytes.fromhex(s["hash"]))).hex() == s["hash"]
assert "integration-test-screen" not in p.read_text()
'
before=$(run sha256sum /opt/dbaba/dbaba.json)
run test -f /opt/dbaba/data/dbaba.db
run touch /opt/dbaba/data/update-probe
run touch /opt/dbaba/old-release-probe
# Update while the existing service still owns its saved port.
health
update_output=$(docker exec -i -w /src -e DBABA_SRC=. "$container" bash deploy/onprem/install.sh </dev/null)
printf '%s\n' "$update_output"
grep -Fq "Waiting for the saved screen port to close" <<<"$update_output"
health
[[ "$(run sha256sum /opt/dbaba/dbaba.json)" == "$before" ]]
run test -f /opt/dbaba/data/update-probe
run test ! -e /opt/dbaba/old-release-probe

printf 'yes\n' | docker exec -i "$container" bash /opt/dbaba/deploy/onprem/uninstall.sh
run test ! -e /opt/dbaba
run test ! -e /etc/systemd/system/dbaba.service
run test ! -e /etc/systemd/system/multi-user.target.wants/dbaba.service
if run systemctl is-active --quiet dbaba.service; then
  echo "Please review the remaining service."
  exit 1
fi
if run curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:8765/api/health; then
  echo "Please review the remaining listener."
  exit 1
fi
if run getent passwd dbaba >/dev/null; then
  echo "Please review the remaining service account."
  exit 1
fi
echo "Install, update, loopback health, saved settings and uninstall checks passed."
