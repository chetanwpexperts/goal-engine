#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
command -v docker >/dev/null || { echo "Please install Docker first." >&2; exit 1; }
tag="dbaba-test-$$"
container="$tag"
volume="$tag-data"
cleanup() {
    docker rm -f "$container" >/dev/null 2>&1 || true
    docker volume rm "$volume" >/dev/null 2>&1 || true
    docker image rm "$tag" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker build -f deploy/onprem/docker/Dockerfile -t "$tag" .
size=$(docker image inspect --format '{{.Size}}' "$tag")
echo "Image size: $size bytes"
[[ "$size" -lt 500000000 ]] || { echo "Image exceeds 500 MB." >&2; exit 1; }
docker volume create "$volume" >/dev/null
# Docker chooses a free host port, restricted to IPv4 loopback.
docker run -d --name "$container" -p 127.0.0.1::8765 \
    -v "$volume:/data" -e DBABA_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')" \
    "$tag" >/dev/null
wait_healthy() {
    for ((i=0; i<120; i++)); do
        state=$(docker inspect --format '{{.State.Status}} {{.State.Health.Status}}' "$container")
        case "$state" in
            "running healthy") return 0 ;;
            exited*|dead*|*" unhealthy") break ;;
        esac
        sleep 2
    done
    echo "DBaba did not become healthy." >&2
    return 1
}
wait_healthy
binding=$(docker port "$container" 8765/tcp)
[[ "$binding" =~ ^127\.0\.0\.1:[0-9]+$ ]] || { echo "Unexpected host port binding." >&2; exit 1; }
host_port=${binding##*:}
docker inspect --format '{{json .NetworkSettings.Ports}}' "$container" | python3 -c '
import json,sys
ports=json.load(sys.stdin)
assert ports == {"8765/tcp":[{"HostIp":"127.0.0.1","HostPort":sys.argv[1]}]}, "Unexpected published ports"
' "$host_port"
docker exec "$container" python -c '
import os,stat,json
assert os.getuid()!=0
p="/data/dbaba.json"
assert stat.S_IMODE(os.stat(p).st_mode)==0o600
c=json.load(open(p))
assert c["port"]==8765 and c["version"]==1
h=c["screen_password"]
assert (h["algorithm"],h["n"],h["r"],h["p"])==("scrypt",16384,8,1)
assert len(bytes.fromhex(h["salt"]))==16 and len(bytes.fromhex(h["hash"]))==64
assert os.path.isfile("/data/dbaba.db")
'
python3 - "$host_port" <<'PY'
import http.cookiejar
import json
import sys
import urllib.request

base = "http://127.0.0.1:" + sys.argv[1]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                     urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def post(path, body):
    request = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    with opener.open(request, timeout=60) as response:
        return json.load(response)
assert post("/api/new-chat", {}).get("ok") is True
reply = post("/api/message", {"text": "what is an index"}).get("reply")
assert isinstance(reply, str) and reply.strip(), "Expected a non-empty reply."
PY
checksum() {
    docker exec "$container" python -c 'import hashlib; print(hashlib.sha256(open("/data/dbaba.json","rb").read()).hexdigest())'
}
before=$(checksum)
docker restart "$container" >/dev/null
wait_healthy
[[ "$(checksum)" == "$before" ]] || { echo "Saved configuration changed after restart." >&2; exit 1; }
[[ "$(docker port "$container" 8765/tcp)" == "$binding" ]]
echo "Docker checks passed."
