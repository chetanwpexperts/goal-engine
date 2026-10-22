# Database knowledge: nightly build rules

Goal Engine solves database problems **without AI**. Its "brain" is the knowledge in `app/dbknow/*.json`.
A scheduled session adds to it every night. These rules are for that session (and any human doing the same work).

## Never
- Never look at, connect to, or collect anything from any user's database or Goal Engine's production data.
  Knowledge comes only from **official public documentation** (dev.mysql.com, mariadb.com/kb, postgresql.org/docs, learn.microsoft.com, docs.oracle.com, sqlite.org).
- Never push to `main`, never deploy, never touch the server. Work only on the `db-knowledge` branch.
- Never add AI/LLM calls or paid APIs.
- Never weaken a safety rule (read-only checks, approval + Undo, privacy wipes). If a task seems to need that, stop and write it in LOG.md for Chetan.

## Each run
1. `git checkout db-knowledge && git pull` (create it from `main` if missing; merge `main` in if it moved ahead).
2. `bash scripts/setup_test_dbs.sh` (local MariaDB + PostgreSQL test databases), `.venv` with `pip install -r requirements.txt pytest httpx`.
3. Take the **first unchecked item** in `BACKLOG.md`. Do one item well rather than many badly (about 10–40 entries per run).
4. Read the official docs for it. Add entries to the right JSON file. **Every entry needs an `https://` `source` link** to the page it came from.
   - Plain-language text in both English (`*_en`) and simple Hinglish (`*_hi`), short, for a small-business owner.
   - Query templates must be read-only (`SELECT`/`WITH`), use `{placeholders}`, and be written for each engine they support.
5. Add or extend tests in `tests/test_dbknow.py` when a new kind of entry needs it. Templates are executed against the real test databases.
6. Run the **whole** suite: `.venv/bin/python -m pytest -q`. Everything must pass. If something fails that you can't fix, revert your change.
7. Tick the item in `BACKLOG.md`, add a dated entry to `LOG.md` (what was added, how many entries, anything Chetan should decide), commit, push `db-knowledge`.

## File formats
See `app/dbknow/__init__.py` (`REQUIRED`, `FIX_TYPES`) and the existing entries. `dbknow.validate()` must return `[]`.
