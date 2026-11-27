# DBaba local API

All requests target `http://127.0.0.1:<port>` (port from dbaba.json). Do not use proxies, redirects or remote API hosts. For a server installation, use a verified SSH tunnel. No cloud AI, telemetry or paid service is needed.

Use Authorization: Bearer <api key> and Content-Type: application/json. Keep the key in DBABA_API_KEY or a private key file (CLI: ~/.config/dbaba/key, mode 0600); desktop uses OS keychain. Never log credentials. Examples below assume BASE=http://127.0.0.1:8765; change the port to match config.

## Start and setup

Run `dbaba serve --port 8765 --config /path/to/private/dbaba.json`. The local engine prints DBABA-SETUP-TOKEN followed by a token only before setup is complete. Wait up to 60 seconds for health. Setup routes accept X-DBaba-Setup instead of bearer authentication, only on loopback before setup, and return 409 after setup. Keep the token in memory and do not log stdout. The API key from save is shown once; store securely.

| Method and path | Body | Response |
|---|---|---|
| GET /api/health | none | {ok, version} |
| GET /api/state | none | {version, setup_done, database, alerts, last_scan} |
| POST /api/setup/detect | none | {found: [{engine, host, port, how}]} |
| POST /api/setup/test | engine, host, port, database, user, password | {ok, version, reason} |
| POST /api/setup/save | same as test plus screen_password | {ok, api_key} |
| POST /api/alerts/{id}/ack | none | {ok} |
| POST /api/report/scan | none | {started} |
| GET /api/report | none | {at, score, engine, sections} |
| GET /api/report.html | none | downloadable HTML |
| GET /api/jobs | none | {jobs} |
| POST /api/jobs/{id} | enabled, at | updated job |
| POST /api/new-chat | none | starts a new conversation |
| POST /api/message | text | {reply, ...} |

Database state has engine, host, port, name, connected. Alerts have id, level, title, text, at, acknowledged. Last scan is null or {at, score}. Scores are 0–100. Report sections have id, title, items; each item has id, severity, title, what, why, fix (null or {sql, needs_approval}). Severity is critical, warning or info. Display fixes without executing them. Jobs have id, name, enabled, every (5min/day/week), at (HH:MM or null), last_run (null or {at, ok, text}). Scans are asynchronous; retrieve the report later.

## CLI

```sh
dbaba status
dbaba alerts
dbaba alerts --ack ALERT_ID
dbaba report --scan
dbaba report --html report.html
dbaba jobs
dbaba jobs JOB_ID --on --at 09:30
dbaba support-report
```

Exit codes: 0 OK, 1 problem found, 2 unable to complete the request. The old support ZIP is now support-report.

## Curl examples

Set BASE and read credentials privately. Do not enable shell tracing. Use actual IDs returned by state/jobs; the example IDs below are placeholders. Setup JSON files must be private and created locally; remove them when finished. The setup save response contains a secret and should not be printed or logged.

```sh
BASE=http://127.0.0.1:8765
read -rs DBABA_API_KEY
export DBABA_API_KEY
api() { curl -q --noproxy '*' --fail --silent --show-error --max-time 30 -H "Authorization: Bearer $DBABA_API_KEY" -H 'Content-Type: application/json' "$@"; }
api "$BASE/api/health"
api "$BASE/api/state"
api -X POST "$BASE/api/alerts/ALERT_ID/ack"
api -X POST "$BASE/api/report/scan"
api "$BASE/api/report"
(umask 077; api "$BASE/api/report.html" > report.html)
api "$BASE/api/jobs"
api -X POST -d '{"enabled":true,"at":"09:30"}' "$BASE/api/jobs/JOB_ID"
api -X POST "$BASE/api/new-chat"
api -X POST -d '{"text":"How do indexes work?"}' "$BASE/api/message"
```

For setup, use a private JSON file containing engine, host, port, database, user, password; save also requires screen_password. Supply the token from the local backend privately:

```sh
read -rs SETUP_TOKEN
setup() { curl -q --noproxy '*' --fail --silent --show-error --max-time 30 -H "X-DBaba-Setup: $SETUP_TOKEN" -H 'Content-Type: application/json' "$@"; }
setup -X POST "$BASE/api/setup/detect"
setup -X POST --data-binary @test-private.json "$BASE/api/setup/test"
(umask 077; setup -X POST --data-binary @save-private.json "$BASE/api/setup/save" > setup-result-private.json)
unset SETUP_TOKEN
```

Move the returned API key into protected storage and remove the private setup files. Use a separate cookie jar for each chat if the engine uses visitor cookies.

The examples are contract-derived; live engine compatibility must be checked once the server implementation lands.
