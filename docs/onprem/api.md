# DBaba self-hosted API

DBaba helps you check SQL and ask database questions about MySQL, MariaDB and PostgreSQL using its local knowledge. Write your questions in English, Hinglish or Hindi.

## Connection

Base URL: `http://127.0.0.1:<port>`. The examples use port 8000; replace it with your running service's port.

Send these headers:

```http
Authorization: Bearer <api key>
Content-Type: application/json
```

Bearer authentication is the planned on-premises interface. Keep the key in a local environment variable. The current chat routes use a visitor cookie (`ge_vid`); retain response cookies and send them with subsequent requests. Use a separate cookie jar per conversation.

For the current application, run with `ENGINE_MODE=ml`, `SITE_MODE=open`, `EXPOSE_INTERNALS=0` and `COOKIE_SECURE=false` for local HTTP. With its application database configured, start it using:

```bash
ENGINE_MODE=ml SITE_MODE=open EXPOSE_INTERNALS=0 COOKIE_SECURE=false uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Endpoints

### POST /api/new-chat

Start a fresh anonymous conversation. Request body: none.

HTTP 200:

```json
{"ok": true}
```

The response resets the visitor cookie and remembers earlier visitor IDs in a separate cookie. For a new anonymous client, begin with an empty cookie jar and use the cookies from the next request onward. The next message can create a goal; this endpoint returns an acknowledgement rather than a goal ID.

Errors: see shared HTTP errors below.

### POST /api/message

Send a SQL query or a database question.

Request body:

```json
{"text": "MySQL: SELECT * FROM orders WHERE customer_id = 42;"}
```

`text` is a required string. Supply between 1 and 10,000 characters after trimming whitespace (the upper limit is configurable through `MAX_MESSAGE_CHARS`). The optional `goal_id` is an integer or null; use the ID returned in `goal.id` to focus a follow-up on that goal. Omit it for the initial message.

The local message handler returns HTTP 200. Its usual response includes:

| Field | Meaning |
| --- | --- |
| reply | Answer text to display |
| relation | Conversation action |
| goal | Current goal object or null |
| goals | List of goal summaries |
| options | Suggested responses |
| next | Suggested next questions |
| ask_words | Integer supplied by the conversation handler |
| agents | Agent details |
| offer_expert | Boolean |
| remaining_today | Current daily counter display |

A goal summary contains `id`, `title`, `status` and `updated_at`. A public goal object also includes `detected_language`, `known_facts`, `missing_info`, `card`, `cards` and `created_at`. Additional details depend on server configuration.

For a small reproducible response example, send `{"text":"list goals"}` in a fresh anonymous session. The following response is derived directly from the handler's literal return values and default settings (rather than captured from a running instance):

```json
{
  "reply": "You don't have any goals yet. Tell me what's wrong.",
  "relation": "list",
  "goal": null,
  "goals": [],
  "remaining_today": 25
}
```

This special list command returns a smaller payload than a SQL message. For SQL and questions, display `reply` and use optional fields when present. The answer and goal details depend on the query, language and conversation.

Errors:

- 400: supply text or shorten the message. Example: `{"error":"Please write something first."}`.
- 422: supply a JSON object with a string `text` and, when used, an integer or null `goal_id`.
- 429: allow a minute between bursts of requests. The response contains `error`.
- 503: the service can return an `error` message; check its local configuration and availability.

Local mode uses per-minute request limits (20 per visitor, 60 per IP and 600 across the service). The `remaining_today` display is a daily counter, separate from those limits.

## Tell DBaba which database

DBaba uses the database named in your message, a database remembered in the conversation, or clues in the SQL syntax. When it needs your choice, the English `reply` starts with "Which database is this for?" and `options` contains:

```json
["MySQL", "MariaDB", "PostgreSQL", "SQL Server", "Oracle", "Not sure"]
```

In Hindi and Hinglish the last option is "Pata nahi". SQL Server and Oracle choices provide general SQL guidance and engine-specific notes.

There are two ways to provide the database:

1. Name it in the message (recommended for scripts):

   ```json
   {"text": "MySQL: SELECT * FROM orders WHERE customer_id = 42;"}
   ```

   Or use natural wording:

   ```json
   {"text": "this is a postgres query: SELECT * FROM orders WHERE customer_id = 42;"}
   ```

2. Answer the database question. Send another POST to `/api/message` using the same cookie jar and the previous response's `goal.id`. For example, if that ID is 42:

   ```json
   {"text": "MySQL", "goal_id": 42}
   ```

   DBaba remembers the choice and continues checking the original query. Use the actual goal ID and a choice from the returned `options`.

### GET /api/state

Read the conversation using the same cookie jar. Request body: none.

HTTP 200 for an empty anonymous session with default settings:

```json
{
  "goals": [],
  "current": null,
  "agents": [],
  "messages": [],
  "remaining_today": 25
}
```

`current` holds the most recently updated goal when available. `messages` contains up to 40 recent messages in chronological order, each with `role`, `text`, `relation` and `goal_id`. The goal ID can be null.

Errors: see shared HTTP errors below.

### GET /api/health

Check service availability. Request body: none.

HTTP 200 with the default public response:

```json
{"ok": true}
```

This is a service-level check. Use a chat request to check the complete conversation flow.

Errors: see shared HTTP errors below.

### Shared HTTP errors

- 503: chat routes return `{"error":"Goal Engine is coming soon"}` while site access is closed. Set `SITE_MODE=open` for your local instance. The health route remains available in this mode.
- 422: FastAPI returns validation details in a `detail` array for requests requiring input validation.
- 500: check the service logs and application database configuration when the server encounters an unexpected problem.

Authentication-specific status codes and payloads will follow the on-premises authentication implementation. The current source defines the chat contract above.

## First 5 minutes

Set your local connection values (provide the key in your shell environment):

```bash
export DBABA_URL=http://127.0.0.1:8000
read -rs -p 'DBaba API key: ' DBABA_API_KEY
echo
export DBABA_API_KEY
```

1. Check a MySQL query: run `bash examples/dbaba.sh`. To check a PostgreSQL query instead, run `DBABA_DB=PostgreSQL bash examples/dbaba.sh 'SELECT * FROM orders;'` (or pass `PostgreSQL` as the second argument).
2. Ask a database question in a fresh session: run `bash examples/dbaba.sh 'How can I add an index in PostgreSQL?'`. The script sends the question unchanged.
3. Check that the service is up:

```bash
curl --fail --silent --show-error \
  -H "Authorization: Bearer $DBABA_API_KEY" \
  "$DBABA_URL/api/health"
```

## Example clients

Run from the repository root. Each script starts an anonymous chat, retains cookies, sends a query and prints the reply. Each accepts an optional SQL query or question as its first argument and a database name as its optional second argument. The second argument takes precedence over `DBABA_DB`, which defaults to `MySQL`. For SQL, each script sends `database: query`. For natural-language questions, it sends the original text unchanged. The examples use a simple leading SQL keyword check for common statements; for other SQL formats, include the database name explicitly in the first argument. Already named messages are passed through unchanged.

- Bash: `bash examples/dbaba.sh` (curl and jq)
- Python: `python -m pip install requests`, then `python examples/dbaba.py`
- Node.js: `node examples/dbaba.js` (Node.js 22 or later)
- PHP: `php examples/dbaba.php` (PHP CLI with the cURL extension)

Response examples above are source-derived; values such as the reply, goal IDs and counters vary by session.
