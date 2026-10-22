# Microsoft knowledge: nightly build rules

Goal Engine's Microsoft agent solves Excel, Word, PowerPoint, Outlook, OneDrive, Teams and Windows problems **without AI**.
Its brain is `app/officeknow/*.json`. A scheduled session adds to it every night on the `office-knowledge` branch.

## The rule that matters most
The agent **does the work**, it doesn't hand people a list of steps. Every fix is one of:
- `auto`: the agent does it on the uploaded file (one of `AUTO_FIXES` in `app/officeknow/__init__.py`) and the person downloads the fixed copy.
- `steps`: only when nobody but the person can do it (their Outlook settings, their printer). Maximum 3 short steps, with the exact menu names.
- `advice`: a short explanation when there's nothing to do.
If a fix could be automatic but the needed `AUTO_FIXES` type doesn't exist yet, write it as `steps` and add a line to "Auto fixes wanted" in BACKLOG.md.

## Sources
- **Truth: official documentation.** Every entry's `source` is a page on support.microsoft.com or learn.microsoft.com (support.google.com for Google Sheets).
- **The teachers (how real people describe problems, what the cause was, what fixed it):** Reddit r/excel (Solved / Unsolved flairs), MrExcel Message Board, Microsoft Community Hub and Microsoft Q&A, Chandoo.org forums, Stack Overflow (`excel`, `excel-formula`, `vba`, `csv`, `ms-word`, `outlook`), Analytics Vidhya, Kaggle discussions about messy data, Maven Analytics case-study topics.
  - Ideas and wording only. Never copy posts, answers, formulas or code (Stack Overflow is CC BY-SA, forums are copyrighted). Write our own entry and put the thread links in `seen_at`.
  - Read pages normally; no bulk scraping. **Never download or open workbooks or files posted on forums** (they can hold macros).
- **Your own experience as Claude** with everyday Office questions counts: our own entry, every claim matched to an official page.

## Goal
Anyone can get any Microsoft problem solved without AI, in English, Hinglish or Hindi, typed or spoken.

## Never
- Never look at or collect anyone's files or Goal Engine's production data.
- Never push to `main`, never deploy, never touch the server. Work only on `office-knowledge`.
- Never add AI/LLM calls or paid APIs. Never weaken a test or a safety rule.
- Never write how to break passwords or protection on files the person doesn't own. "I forgot my own file's password" gets the official recovery options only.

## Each run
1. `git checkout office-knowledge && git pull` (merge `origin/main` in if it moved; abort on conflict).
2. Create `.venv`, `pip install -r requirements.txt pytest httpx`.
3. Read the learning feed (see the scheduled task) and cover requests with brain `office` first.
4. Take the first unchecked item in BACKLOG.md, research it, add entries to `issues.json` / `formulas.json`.
5. **Prove it works:** every entry has 3+ `test_phrases`, NEW sentences a normal person would say, at least one in English, one in Hinglish and one in Hindi script. `tests/test_officeknow.py` checks the brain recognises them. If it doesn't, add richer, more varied `symptoms`/`examples` until it does. Never weaken a test phrase. Add the error values/codes a problem shows in `codes` (#REF!, 0x8004010F, ...).
6. Run the whole suite `.venv/bin/python -m pytest -q`. Everything must pass, or revert your change.
7. Tick the item, add a dated entry to LOG.md, commit, push `office-knowledge`.

## File formats
See `app/officeknow/__init__.py` (`REQUIRED`, `CATEGORIES`, `APPS`, `AUTO_FIXES`) and the existing entries.
