# Goal Engine

A person describes a problem in their own words (English, Hindi, Hinglish). The system
turns it into a **persistent, structured goal** and picks the single most useful next step.
The goal lives outside the chat, so it doesn't drift as the conversation goes on.

This is Phase 1–2 of the Universal AI Problem-Solving Platform:
text request → structured goal → goal kept up to date across messages.

## Engines

`ENGINE_MODE=ml` (default) runs the project's own machine-learning engine, with no external AI:

- **Clean**: Hinglish spelling normaliser; Hindi script (e.g. from voice typing) is transliterated to Hinglish letters.
- **Classify**: scikit-learn TF-IDF (character + word n-grams) + logistic regression over 12 problem types.
  Seed examples live in `app/ml/seed_data.py`; the model retrains automatically when they change.
- **Extract**: rule-based slot extraction (business type, city, website, since when, budget, skills, ...).
- **Converse without loops**: a short, vague first message gets ONE request for a ~100-word description
  (typed or spoken, with a live word counter). After that, at most one question per reply and two per goal,
  always with tap answers. "Pata nahi" is accepted: a default is assumed and shown as "Assumed ...".
- **Act**: once nothing critical is missing, it gives a concrete, personalised first answer (`app/ml/answers.py`).
- **Learn**: every message is stored with its predicted type and confidence, ready for the learning loop.

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
