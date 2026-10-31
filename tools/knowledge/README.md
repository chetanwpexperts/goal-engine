# Growing DBaba's SQL knowledge (no Claude, no paid API)

docs -> local model (Ollama, qwen2.5-coder:7b) drafts rules -> MySQL 8.4, MariaDB 11.4, PostgreSQL 16 decide.

A drafted rule is kept only if its broken query fails on a real engine and its fixed query runs there. Engines and
error codes are recorded from the databases. A rule's pattern is kept only if it finds the broken query, misses the
fixed one and never fires on hundreds of valid generated queries (tests/test_learned_rules.py checks again in CI).

On the Mac, from the repo folder:

    python3 -m venv .kvenv && .kvenv/bin/pip install -r tools/knowledge/requirements.txt
    docker compose -f tools/knowledge/docker-compose.yml up -d
    .kvenv/bin/python tools/knowledge/harvest.py --limit 2     # quick try
    .kvenv/bin/python tools/knowledge/harvest.py               # everything in sources.json (resumes)
    git add app/knowledge/sql_rules/learned.json && git commit -m "Learned SQL rules" && git push

Add more doc pages to sources.json any time. Stop the engines: docker compose -f tools/knowledge/docker-compose.yml down
