# DBaba local contract (S4)

What DBaba serves when it runs on the person's own server or laptop (`app/local`). The CLI (`dbaba_cli/`), the desktop
app (`desktop/`), the installer and Docker image (`deploy/onprem/`) and the docs (`docs/onprem/`) build against this file;
if anything elsewhere differs, this file wins. Tests: `tests/test_local.py`.

## Starting it

```
python -m app.local serve --port <port> --config <path to dbaba.json> [--host 127.0.0.1]
python -m app.local reset --config <path>          # setup again; the next start prints a new setup token
```

* `--config` defaults to `$DBABA_CONFIG`, else `./dbaba.json`. A missing file is created (mode 0600).
* `--port` defaults to the file's `port`. `--host` defaults to `127.0.0.1`. Use `0.0.0.0` only inside a container whose
  port is published on the host's `127.0.0.1`; a non-loopback host prints a warning.
* While setup isn't done, the first line on stdout is `DBABA-SETUP-TOKEN <token>` (a new token on every start and
  after a reset).
* DBaba's own working database defaults to `data/dbaba.db` next to the file (`DATABASE_URL` overrides it).
* No AI provider is ever used: AI keys in the environment are cleared, and `ENGINE_MODE`, the daily caps and the
  website's switches are forced to local values whatever the environment says.

## dbaba.json

The format the installer and the Docker entrypoint already write. DBaba keeps every key it doesn't know.

```json
{"version": 1, "port": 8765,
 "screen_password": {"algorithm": "scrypt", "salt": "<hex>", "hash": "<hex>", "n": 16384, "r": 8, "p": 1} | null,
 "database": {"type": "mysql|mariadb|postgresql", "host": "127.0.0.1", "port": 3306, "name": "...",
              "user": "...", "password": "...", "socket": "<optional>"} | null,
 "api_keys": [{"name": "desktop", "hash": "<sha256 hex of the key>", "created": "<ISO-8601>"}],
 "setup_done": true,
 "jobs": {"scan": {"enabled": true, "at": "02:40"}},
 "allowed_hosts": ["<optional extra Host names for the screen>"],
 "updates": {"enabled": true}, "alerts": {"email": null}}
```

* `setup_done` missing (an older file): set up when `database` is present.
* An API key is shown once (in the `/api/setup/save` answer); only its SHA-256 is stored.
* Writes are atomic (temporary file, mode 0600, rename).

## Who may call what

| Caller | How | May call |
|---|---|---|
| anyone who reaches the port | nothing | `GET /api/health`, `POST /api/login`, `GET /` |
| an API key | `Authorization: Bearer <key>` | everything below |
| the screen | `POST /api/login {"password"}` sets the `dbaba_session` cookie (HttpOnly, SameSite=Strict, 12 h idle) | everything below |
| (an API key can't create or revoke keys: only a logged-in screen can) | | |
| the setup token | `X-DBaba-Setup: <token>`, **only from 127.0.0.1**, only while setup isn't done | `GET /api/state` and `/api/setup/detect|test|save` |

* The `Host` header must be `127.0.0.1`, `localhost` or `::1` (any port), or a name in `allowed_hosts` /
  `$DBABA_ALLOWED_HOSTS`; anything else gets 403 (no DNS rebinding).
* A cookie-authenticated `POST` must send `Content-Type: application/json`, otherwise 415.
* 5 wrong passwords from one address in 10 minutes give 429 for the rest of those minutes.
* `POST /api/login` answers 409 when the file has no screen password yet (finish setup first).
* Errors are JSON `{"error": "<plain words>"}` with the status code.

## Endpoints

| Method and path | Answer |
|---|---|
| `GET /api/health` | `{"ok": true, "version": "x.y.z"}` |
| `POST /api/login` `{"password"}` | `{"ok": true}` and the cookie; 401 wrong; 409 no password yet; 429 locked |
| `POST /api/logout` | `{"ok": true}` |
| `GET /api/state` | `{"version", "setup_done", "has_password", "database": {"engine", "host", "port", "name", "connected"} \| null, "alerts": [Alert], "last_scan": {"at", "score"} \| null}` |
| `POST /api/setup/detect` | `{"found": [{"engine", "host", "port", "how"}]}` |
| `POST /api/setup/test` `{"engine", "host", "port", "database", "user", "password"}` | `{"ok", "version", "reason", "warning"?}` (`reason` in plain words when not ok; `warning` when the user can change data) |
| `POST /api/setup/save` `{same, "screen_password"?}` | `{"ok": true, "api_key", "warning"?}` or `{"ok": false, "reason"}`. `screen_password` (8+ characters) is required when the file has none. Starts the first scan and a health check. |
| `POST /api/setup/reset` (key or session) | `{"ok": true}`; setup is open again and a new token is printed |
| `POST /api/database` (key or session) `{"engine"?, "host"?, "port"?, "database"?, "user"?, "password"?}` | After setup: a new address for the same database (an SSH tunnel's new local port, a moved server). Missing fields keep the saved ones; a missing `password` keeps the saved password. Tested first: `{"ok": true, "version", "warning"?}` and saved, or `{"ok": false, "reason"}` (status 200) and nothing changed. 409 before setup. |
| `GET /api/keys` (key or session) | `{"keys": [{"id", "name", "created"}]}`; `id` is the first 12 hex characters of the key's SHA-256, never the key |
| `POST /api/keys` `{"name"}` (**session only**) | `{"api_key", "keys"}`; the key is shown this once. 400 for a name outside letters, digits, space, `.` `-` `_` (1–40) |
| `POST /api/keys/{id}/revoke` (**session only**) | `{"ok": true, "keys"}`; the key is refused from the next request. 404 unknown id |
| `GET /api/report` | `{"at", "score", "engine", "version", "pending", "changes", "sections": [{"id", "title", "items": [Item]}]}`; before the first scan `at` and `score` are null. `changes`: `{"first": true}` for the first report, else `{"first": false, "score_before", "at_before", "new": [titles], "gone": [titles]}` (warnings and criticals only) |
| `GET /api/report.html` | the report as a downloadable HTML file |
| `POST /api/report/scan` | `{"started": bool}` (false while a scan runs) |
| `GET /api/jobs` | `{"jobs": [Job]}` |
| `POST /api/jobs/{id}` `{"enabled": bool, "at": "HH:MM" \| null}` | the Job; 400 bad body; 404 unknown id |
| `POST /api/alerts/{id}/ack` | `{"ok": true}`; 404 unknown id |
| `POST /api/new-chat` | `{"ok": true}` |
| `POST /api/message` `{"text"}` (up to 10,000 characters) | `{"reply", "relation"}` |

* **409** on every `/api/setup/*` call except `reset` once setup is done.
* **Alert**: `{"id", "level": "info|warning|critical", "title", "text", "at": ISO-8601, "acknowledged": bool}`. An alert
  is raised once per problem, and stays until acknowledged. When the problem goes away, an `info` alert says so.
* **Item**: `{"id", "severity": "critical|warning|info", "title", "what", "why", "fix": {"sql", "needs_approval": true} | null}`.
  A fix is only a suggestion; nothing in S4 changes the database.
* **Job**: `{"id", "name", "enabled", "every": "5min|day", "at": "HH:MM" | null, "last_run": {"at", "ok", "text"} | null}`.
  S4 has `health` (every 5 minutes) and `scan` (daily, default 02:40 local time); S6 adds backups.
* **Report sections** (S5), worst first; the score is 100 − 25 per critical − 5 per warning:
  `now` (connections against the limit, queries over a minute, who waits on whom, deadlocks), `slow` (the slowest queries
  from performance_schema / pg_stat_statements, each read by DBaba's query checker against the real tables; or why they
  can't be seen), `indexes` (duplicate and covered, unused after 7 days of counts, foreign keys without an index, big tables
  read without one), `tables` (no primary key, wasted space, dead rows, stale statistics, biggest tables), `settings`
  (memory for data against this machine's, connections near the limit, the slow log), `backups` (point-in-time or not),
  `security` (DBaba's user can change data, remote root, empty passwords, anonymous users, open network, md5, versions out of
  support), `server` (version). A new critical item also raises an alert.

## Files DBaba keeps (next to dbaba.json, mode 0600)

`data/dbaba.db` (chat history), `data/local/alerts.json` (newest 200), `data/local/runs.json`, `data/local/report.json`,
`data/local/chat_id`. Nothing is sent anywhere.
