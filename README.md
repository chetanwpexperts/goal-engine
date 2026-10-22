# Goal Engine

A person describes a problem in their own words (English, Hindi, Hinglish). The system
turns it into a **persistent, structured goal** and picks the single most useful next step.
The goal lives outside the chat, so it doesn't drift as the conversation goes on.

This is Phase 1–2 of the Universal AI Problem-Solving Platform:
text request → structured goal → goal kept up to date across messages.

## Engines

`ENGINE_MODE=ml` (default) runs the project's own machine-learning engine, with no external AI:

- **Clean**: Hinglish spelling normaliser; Hindi script (e.g. from voice typing) is transliterated to Hinglish letters.
- **Classify**: scikit-learn TF-IDF (character + word n-grams) + logistic regression over 70 real problem types
  (business, developer/tech, career, education, money, home, vehicle, government papers, consumer, legal,
  health, farming, personal, travel). Examples and answer steps live in `app/ml/knowledge/` and
  `app/ml/seed_data.py`; the model retrains automatically when they change.
  Measured on messages it never saw (`tests/data/holdout*.py`): ~79-87% exactly right, ~92-97% in the top 3.
- **When unsure**: below 50% confidence it also shows "Did I get it wrong?" with the next two guesses;
  one tap corrects the same goal (and that correction is stored for learning).
- **Not learned yet**: still creates a goal, pulls out facts and gives a general first plan. No question loops.
- **Safety**: crisis messages get support and helplines (Tele-MANAS 14416, 112), emergencies get 112/108;
  health, legal and money answers carry a "check with a professional" note.
- **Extract**: rule-based slot extraction (business type, city, website, since when, budget, skills, ...).
- **Converse without loops**: a short, vague first message gets ONE request for a ~100-word description
  (typed or spoken, with a live word counter). After that, at most one question per reply and two per goal,
  always with tap answers. "Pata nahi" is accepted: a default is assumed and shown as "Assumed ...".
- **Act**: once nothing critical is missing, it gives a concrete, personalised first answer (`app/ml/answers.py`).
- **Learn**: every message is stored with its predicted type and confidence, ready for the learning loop.

## Agents: the system does the work

The user describes the problem; the system assigns an agent, shows the plan in plain words
("Main ye karunga"), and runs it on one tap or a "haan". The user never configures anything.

- **Building blocks**: connect -> check -> act -> when -> verify (`app/agents/core.py`).
- **Assignment**: the goal's problem type picks the recipe; inputs (a URL, a pasted query) come
  from the conversation; the agent asks only for what it's missing.
- **Trust levels**: Level 0 needs no access (public URL, pasted query). Level 1 (database automation)
  will use a read-only connector on the user's own server, so passwords never leave it.
- **Agents now**: Website Checker (security, speed, phone layout, errors, Google basics, contact
  buttons; with evidence), Website Watch (daily, scheduled), SQL Query Checker (real bugs,
  index-blocking patterns, rewritten query, exact index commands). Database Automation is planned.
- **Safety**: user URLs are checked against private/internal addresses on every redirect (SSRF);
  agent runs are rate-limited per visitor; scheduled runs are claimed once even with several workers.

## Automation agents: one sentence -> a working agent

"Jinka payment baaki hai unko har Monday reminder chala jaye" becomes an agent built from blocks:
when (schedule, India time) -> read (Google Sheet link or pasted CSV) -> find the right rows
(due soon / overdue / tomorrow's appointments / inactive N days / birthdays / yesterday's visitors /
amount due) -> write each message -> send -> summary to the owner. Reports work the same way
but go to the owner.

- **Planner** (`app/agents/planner.py`): understands payment, fee, appointment, win-back, review,
  birthday and report requests in Hinglish/Hindi/English, plus when ("har Monday 10 baje",
  "har mahine 5 tarikh", "shaam 7 baje"). Optional AI planner (`PLANNER_AI=on`) for anything else.
- **Validator** (`app/agents/spec.py`): only real blocks, needed columns, safe schedules.
- **Columns by meaning** (`app/agents/blocks.py`): "Customer Name", "Amount Due", "naam" all map
  automatically; missing columns are explained with the sheet's own column names.
- **Trust**: preview shows exactly who gets what; nothing goes to customers until the owner
  verifies their email; replies go to the owner (Reply-To); max 50 per run / 100 per day;
  never the same person twice within 20 hours; every email logged.

`ENGINE_MODE=ai` uses Gemini/Groq instead (the path described below).

## How it works (AI mode)

1. **Receive**: the message, plus the visitor's active goals and the last few messages.
2. **Cheap path first**: requests like "show my goals" are answered from the database, with no AI call.
3. **Understand**: the AI router asks Gemini (falling back to Groq) for a strict JSON interpretation.
4. **Validate**: output must match the schema (`app/ai/schema.py`); anything else is rejected, never guessed.
5. **Structure**: the message either creates a new goal, updates an existing one, adds a
   supporting task, or is just chit-chat. A separate problem never gets mixed into an existing goal.

Goal state: original request, detected language, interpreted problem, desired goal, known facts,
missing critical info (max 3), constraints, supporting tasks, confidence, next action.

## Safety

- Daily caps for all visitors together and per visitor, so the free AI quota is never exceeded.
- Each visitor only sees their own goals (anonymous cookie); the AI can't point a message at another visitor's goal.
- User and AI text is always rendered as text, never as HTML.
- API keys live only in `.env` on the server.

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # add GEMINI_API_KEY; for quick local use set DATABASE_URL=sqlite:///local.db
.venv/bin/uvicorn app.main:app --reload
```

Tests: `.venv/bin/pip install pytest httpx && .venv/bin/python -m pytest -q`

## Deploy

See `deploy/`: Supervisor program on port 8098, nginx site for `solve.chetanbuilds.com`,
and `deploy/update.sh` to pull and restart.
