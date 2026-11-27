#!/usr/bin/env bash
set -uo pipefail
target=$1
results=$2
name="dbaba-it-$$"
image="$name-img"
volume="$name-data"
fresh="$name-fresh"
net="$name-net"
other="$name-other"
failed=0
record() { printf '| %s | %s | %s |\n' "$target" "$1" "$2" >> "$results"; }
step() {
  local label=$1; shift
  if "$@"; then record "$label" PASS; else record "$label" FAIL; failed=1; fi
}
require_step() {
  local label=$1; shift
  if "$@"; then record "$label" PASS; else record "$label" FAIL; exit 1; fi
}
s4_step() {
  local label=$1; shift
  local code
  code=$(state_status) || { record "$label" FAIL; failed=1; return; }
  if [[ "$code" == 404 ]]; then record "$label" "WAITING FOR S4"; return; fi
  if "$@"; then record "$label" PASS; else record "$label" FAIL; failed=1; fi
}
cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker volume rm "$volume" "$fresh" >/dev/null 2>&1 || true
  docker network rm "$net" "$other" >/dev/null 2>&1 || true
  docker image rm "$image" >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# Never print generated credentials or saved configuration.
password=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))') || exit 1
export DBABA_PASSWORD="$password"
inside() { docker exec "$name" "$@"; }
# Use a portable Python probe instead of relying on curl output formatting.
health() {
  timeout 60 bash -c 'until docker exec "$1" "$2" -c "$3" >/dev/null 2>&1; do sleep 1; done' bash "$name" "$1" 'import json,urllib.request; o=urllib.request.build_opener(urllib.request.ProxyHandler({})); assert json.load(o.open("http://127.0.0.1:8765/api/health",timeout=2)).get("ok") is True'
}
if [[ "$target" != docker ]]; then
  if [[ "${CI:-}" != true && "${DBABA_ALLOW_PRIVILEGED:-}" != 1 ]]; then
    record "systemd container" "NOT RUN (local privileged opt-in required)"
    exit 1
  fi
  require_step "build clean OS" docker build --build-arg "BASE=$target" -f deploy/onprem/tests/systemd.Dockerfile -t "$image" deploy/onprem/tests
  require_step "start systemd container" docker run -d --name "$name" --privileged --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw --tmpfs /run --tmpfs /run/lock -v "$PWD:/source:ro" "$image"
  if timeout 60 bash -c 'until docker exec "$1" systemctl show-environment >/dev/null 2>&1; do sleep 1; done' bash "$name" && [[ "$(inside cat /proc/1/comm)" == systemd ]]; then
    record "systemd PID 1 and bus" PASS
  else
    record "systemd PID 1 and bus" "NOT RUNNABLE (systemd bus unavailable)"
    exit 1
  fi
  py=$(inside bash -c 'for p in python3 python3.11; do if command -v "$p" >/dev/null && "$p" -c "import sys; assert sys.version_info >= (3,11)" 2>/dev/null; then echo "$p"; exit; fi; done; exit 1') || exit 1
  install_dbaba() { printf '8765\n%s\n%s\n0\n' "$password" "$password" | docker exec -i -e DBABA_SRC=/source "$name" bash /source/deploy/onprem/install.sh; }
  require_step "install" install_dbaba
  step "health within 60s" health "$py"
  step "service active and enabled" inside bash -c 'systemctl is-active --quiet dbaba.service && systemctl is-enabled --quiet dbaba.service'
  step "loopback listener only" inside bash -c 'ss -H -ltn "sport = :8765" | awk '\''{n++; if ($4 != "127.0.0.1:8765") bad=1} END {exit !(n && !bad)}'\'''
  step "permissions and ownership" inside bash -c 'test "$(stat -c "%a %U %G" /opt/dbaba/dbaba.json)" = "600 dbaba dbaba" && test "$(stat -c "%a %U %G" /opt/dbaba/data)" = "700 dbaba dbaba"'
  offline() {
    inside bash -c 'set -e; iptables -I OUTPUT 1 ! -o lo -j REJECT; ip6tables -I OUTPUT 1 ! -o lo -j REJECT; iptables -Z OUTPUT; ip6tables -Z OUTPUT' || return 1
    local rc=0
    health "$py" && sleep 5 && health "$py" || rc=1
    inside bash -c 'set -e; for t in iptables ip6tables; do "$t" -L OUTPUT -v -n -x | awk '\''$3=="REJECT" {n++; if ($1!=0) bad=1} END {exit !(n && !bad)}'\''; done' || rc=1
    inside bash -c 'iptables -D OUTPUT ! -o lo -j REJECT; ip6tables -D OUTPUT ! -o lo -j REJECT' || rc=1
    return "$rc"
  }
  step "egress blocked and no rejected packets" offline
  preserve() {
    local before after
    before=$(inside sha256sum /opt/dbaba/dbaba.json) || return 1
    inside runuser -u dbaba -- sh -c 'printf fixture > /opt/dbaba/data/install-test-marker' || return 1
    install_dbaba || return 1
    after=$(inside sha256sum /opt/dbaba/dbaba.json) || return 1
    [[ "$before" == "$after" ]] && [[ "$(inside cat /opt/dbaba/data/install-test-marker)" == fixture ]] && health "$py"
  }
  step "reinstall preserves config and data" preserve
  uninstall() {
    printf 'y\n' | docker exec -i "$name" bash /opt/dbaba/uninstall.sh &&
    inside bash -c 'test ! -e /opt/dbaba && test ! -e /etc/systemd/system/dbaba.service && ! getent passwd dbaba && ! systemctl is-active --quiet dbaba.service && ! systemctl is-enabled --quiet dbaba.service && test -z "$(ss -H -ltn "sport = :8765")"'
  }
  step "uninstall removes service files and user" uninstall
else
  require_step "build shipping image" docker build -f deploy/onprem/docker/Dockerfile -t "$image" .
  require_step "create isolated networks and volumes" bash -c 'set -e; docker network create "$1"; docker network create "$2"; docker volume create "$3"; docker volume create "$4"' bash "$net" "$other" "$volume" "$fresh"
  start() { docker run -d --name "$name" --network "$net" -p 127.0.0.1:8765:8765 -v "$1:/data" -e DBABA_PASSWORD "$image"; }
  require_step "native entrypoint" start "$volume"
  step "health within 60s" health python
  healthy() { timeout 60 bash -c 'until [[ "$(docker inspect -f "{{.State.Health.Status}}" "$1")" == healthy ]]; do sleep 1; done' bash "$name"; }
  step "Docker healthcheck healthy" healthy
  host_health() { curl --noproxy '*' -fsS --max-time 3 http://127.0.0.1:8765/api/health | python3 -c 'import json,sys; assert json.load(sys.stdin).get("ok") is True'; }
  step "host loopback publishing" bash -c 'test "$(docker port "$1" 8765/tcp)" = 127.0.0.1:8765' bash "$name"
  step "host loopback health" host_health
  peer() { local network=$1; shift; docker run --rm --network "$network" --entrypoint python "$image" "$@"; }
  isolation() {
    local hostip containerip
    hostip=$(ip -4 route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}') || return 1
    [[ -n "$hostip" && "$hostip" != 127.* ]] || return 1
    containerip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$name") || return 1
    [[ -n "$containerip" ]] || return 1
    ! curl --noproxy '*' -sS --max-time 3 "http://$hostip:8765/api/health" >/dev/null || return 1
    peer "$other" -c 'import socket,sys
for host in sys.argv[1:]:
    try:
        with socket.create_connection((host,8765),timeout=3): pass
    except OSError: continue
    raise SystemExit("Unexpected reachable listener")' "$hostip" "$containerip"
  }
  step "non-loopback host and cross-network isolation" isolation
  step "non-root UID and private config" inside python -c 'import os,stat; s=os.stat("/data/dbaba.json"); assert os.getuid()==os.getgid()==s.st_uid==s.st_gid==10001; assert stat.S_IMODE(s.st_mode)==0o600; assert os.stat("/data").st_uid==10001'
  state_status() {
    peer "$net" -c 'import http.client,sys
c=http.client.HTTPConnection(sys.argv[1],8765,timeout=5)
try:
    c.request("GET","/api/state")
    print(c.getresponse().status)
finally:
    c.close()' "$name"
  }
  auth_refusal() {
    peer "$net" -c 'import http.client,sys
c=http.client.HTTPConnection(sys.argv[1],8765,timeout=5)
try:
    c.request("GET","/api/state")
    r=c.getresponse()
    assert r.status in (401,403) or (r.status in (301,302,303,307,308) and "login" in (r.getheader("Location") or "").lower())
finally:
    c.close()' "$name"
  }
  s4_step "same-network unauthenticated request refused" auth_refusal
  checksum() { inside sha256sum /data/dbaba.json; }
  recreate() {
    local before after
    before=$(checksum) || return 1
    inside sh -c 'printf fixture > /data/install-test-marker' || return 1
    docker stop "$name" && docker rm "$name" && start "$volume" && healthy || return 1
    after=$(checksum) || return 1
    [[ "$before" == "$after" && "$(inside cat /data/install-test-marker)" == fixture ]]
  }
  step "stop remove recreate preserves config and data" recreate
  # Check persisted password verifier, without asserting an unimplemented login works.
  step "same password verifier survives recreation" docker exec -e DBABA_PASSWORD "$name" python -c 'import os,json,hashlib,hmac; h=json.load(open("/data/dbaba.json"))["screen_password"]; assert hmac.compare_digest(hashlib.scrypt(os.environ["DBABA_PASSWORD"].encode(),salt=bytes.fromhex(h["salt"]),n=h["n"],r=h["r"],p=h["p"],dklen=64).hex(),h["hash"])'
  # No authenticated screen protocol is documented yet. Fail closed when
  # the endpoint exists until the real S4 login contract is available.
  actual_login() {
    echo "S4 login contract must be implemented in this test before it can pass." >&2
    return 1
  }
  s4_step "same password actual screen login" actual_login
  fresh_start() {
    local before after
    before=$(checksum) || return 1
    docker stop "$name" && docker rm "$name" && start "$fresh" && healthy || return 1
    after=$(checksum) || return 1
    [[ "$before" != "$after" ]] && inside test ! -e /data/install-test-marker
  }
  step "fresh volume starts clean" fresh_start
  remove_docker() { docker stop "$name" && docker rm "$name" && docker volume rm "$volume" "$fresh" && ! docker container inspect "$name" >/dev/null 2>&1; }
  step "remove container and volumes" remove_docker
fi
exit "$failed"
