"""Standing rule A: the user's database decides the answer.

1. NAMED: the person names it ("this is a MySQL query", "postgres error", "SQL Server", "ye MariaDB hai"): answer only for
   that database, and remember it on the goal for the rest of the conversation. A later message naming another database
   switches it.
2. SHOWN: not named, but the text shows it (backticks or LIMIT a,b -> MySQL/MariaDB; ::casts, ILIKE, RETURNING, $1 ->
   PostgreSQL; TOP n, [brackets], GETDATE() -> SQL Server; ROWNUM, seq.NEXTVAL, VARCHAR2 -> Oracle; or the engine's own
   error format): answer for it, say which one was detected and why, with one tap to correct it.
3. NEITHER: ask first (MySQL / MariaDB / PostgreSQL / SQL Server / Oracle / Not sure). Plain "SQL" names the language, not
   a database. Only "Not sure" gets an answer for each engine, labelled.

Everything one engine owns (how it is named, its syntax marks, its error format) is in its dialect file
(app/dbknow/dialects/<engine>.json: 'named', 'query_marks', 'weak_marks', 'error_marks'); this file only reasons over them.
SQL Server, Oracle and SQLite are 'notes_only': recognised, answered with general SQL reasoning plus notes, and said so.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

DIALECTS = Path(__file__).resolve().parent / "dialects"
ORDER = ["mysql", "mariadb", "postgresql", "sqlserver", "oracle", "sqlite"]     # also the order of the choice taps
FAMILY = {"mysql": "mysql", "mariadb": "mysql", "postgresql": "postgresql"}     # the families the lab-tested rules know
KEY = "_db_engine"                                                              # where the goal remembers it (goal.slots)
PENDING = "db_engine"                                                           # goal.pending while the question is open
NOT_SURE = "not_sure"

TAPS = {"en": ["MySQL", "MariaDB", "PostgreSQL", "SQL Server", "Oracle", "Not sure"],
        "hi": ["MySQL", "MariaDB", "PostgreSQL", "SQL Server", "Oracle", "Pata nahi"]}
NOT_SURE_RE = re.compile(r"^\W*(not sure|no idea|don.?t know|dont know|i don.?t know|unsure|any|either|pata nahi|nahi pata|"
                         r"maloom nahi|malum nahi|pta nhi|pata nhi|नहीं पता|पता नहीं|मालूम नहीं)\W*$", re.I)
NEGATED = r"(?:not|nahi|nahin|na|no|नहीं)"
FILLER = set("""it its it's s this is a an the actually wait no nope sorry oh ok okay use using we i am our my on in for switch to
database db server query hai he h hain ye yeh yah wala wali wale ka ki ke mein me pe par hoon hu tha thi actually asal mein
यह ये है हैं का की के में पर वाला वाली डेटाबेस""".split())


@lru_cache(maxsize=None)
def dialect(eid: str) -> dict:
    return json.loads((DIALECTS / f"{eid}.json").read_text(encoding="utf-8"))


def label(eid: str | None) -> str:
    if eid not in ORDER:
        return "Not sure"
    return dialect(eid)["name"]


def notes_only(eid: str | None) -> bool:
    return bool(eid in ORDER and dialect(eid).get("notes_only"))


def family(eid: str | None) -> str | None:
    """'mysql' / 'postgresql' for the engines the lab-tested rules know; None otherwise (not sure, or notes-only)."""
    return FAMILY.get(eid or "")


@lru_cache(maxsize=None)
def _rx(eid: str, key: str) -> list:
    return [re.compile(p, re.I) for p in dialect(eid).get(key, [])]


# ------------------------------------------------------------------ 1. named
def named(text: str) -> str | None:
    """The database the words name, or None. 'SQL' alone is the language, not a database. With two named ('convert this
    mysql query to postgres', 'postgres hai, mysql nahi'), the first one that isn't negated wins."""
    t = text or ""
    hits = []
    for eid in ORDER:
        for rx in _rx(eid, "named"):
            for m in rx.finditer(t):
                before, after = t[max(0, m.start() - 12):m.start()], t[m.end():m.end() + 12]
                if re.search(rf"\b{NEGATED}\s*$", before, re.I) or re.match(rf"\s*{NEGATED}\b\s*(?:(?:hai|h|he)\b|[,.]|$)", after, re.I):
                    continue                              # 'not mysql', 'mysql nahi'
                hits.append((m.start(), eid))
    if not hits:
        return None
    hits.sort()
    first = hits[0][1]
    if first == "mysql" and any(e == "mariadb" for _p, e in hits):
        return "mariadb"                                  # 'MariaDB (a MySQL fork)': the more specific name
    return first


# ------------------------------------------------------------------ 2. shown
def shown(text: str) -> tuple[str | None, str]:
    """(engine, why) from the text's own syntax or error format, or (None, ''). MySQL and MariaDB share their syntax, so a
    MySQL-style query is 'mysql' (said as MySQL / MariaDB) unless an error message says MariaDB."""
    t = _strip_strings(text or "")
    score: dict[str, list[str]] = {}
    for eid in ORDER:
        if eid == "mariadb":
            continue
        why = [m.group(0).strip() for rx in _rx(eid, "query_marks") for m in [rx.search(t)] if m]
        why += [m.group(0).strip() for rx in _rx(eid, "error_marks") for m in [rx.search(text or "")] if m]
        weak = [m.group(0).strip() for rx in _rx(eid, "weak_marks") for m in [rx.search(t)] if m]
        if why:
            score[eid] = why + weak
        elif len(weak) >= 2:
            score[eid] = weak
    if any(rx.search(text or "") for rx in _rx("mariadb", "error_marks")):
        return "mariadb", "MariaDB's own error message"
    if not score:
        return None, ""
    if len(score) > 1:                                    # marks of two databases: the clearer one, or none
        best = sorted(score, key=lambda e: -len(score[e]))
        if len(score[best[0]]) == len(score[best[1]]):
            return None, ""
        score = {best[0]: score[best[0]]}
    eid, why = next(iter(score.items()))
    return eid, ", ".join(dict.fromkeys(w[:30] for w in why))[:90]


def _strip_strings(t: str) -> str:
    return re.sub(r"'(?:[^'\\]|\\.|'')*'", "''", t)


# ------------------------------------------------------------------ the goal remembers it
def remembered(goal) -> dict | None:
    from app.models import loads

    if goal is None:
        return None
    return (loads(goal.slots, {}) or {}).get(KEY)


def remember(goal, eid: str, how: str, why: str = "") -> dict:
    from app.models import dumps, loads

    data = loads(goal.slots, {}) or {}
    rec = {"id": eid, "how": how, "why": why}
    data[KEY] = rec
    goal.slots = dumps(data)
    return rec


def choice(text: str) -> str | None:
    """A reply to the 'which database?' question: an engine id, 'not_sure', or None."""
    t = (text or "").strip()
    if NOT_SURE_RE.match(t):
        return NOT_SURE
    if len(t) > 60:
        return None
    return named(t)


def correction(text: str, goal) -> str | None:
    """'It's PostgreSQL', 'actually mariadb hai', 'no, this is SQL Server': a short message that only names a different
    database than the one remembered (no query, no question about something else)."""
    t = (text or "").strip()
    if len(t) > 80 or re.search(r"\b(select|update|delete|insert|from|where)\b", t, re.I):
        return None
    e = named(t)                                   # only a named database corrects one ('not sure' answers the question)
    rec = remembered(goal) or {}
    if not e or e == rec.get("id"):
        return None
    rest = t
    for rx in _rx(e, "named"):
        rest = rx.sub(" ", rest)
    rest = re.sub(r"[^\w\sऀ-ॿ]", " ", rest)
    left = [w for w in rest.split() if w.lower() not in FILLER]
    return e if len(left) <= 1 else None           # 'it's PostgreSQL', 'ye MariaDB hai', 'no, mysql 8': only the database


def resolve(goal, text: str) -> dict | None:
    """What decides this message's answer: {'id', 'how': 'named'|'detected'|'remembered'|'chosen', 'why'} or None (ask).
    A database named in this message is remembered at once (and switches an earlier one)."""
    n = named(text)
    if n:
        return remember(goal, n, "named") if goal is not None else {"id": n, "how": "named", "why": ""}
    rec = remembered(goal)
    seen, why = shown(text)
    if seen and (not rec or rec.get("how") == "detected"):
        out = {"id": seen, "how": "detected", "why": why}
        if goal is not None:
            remember(goal, seen, "detected", why)
        return out
    if rec:
        return {**rec, "how": rec.get("how") if rec.get("how") in ("named", "chosen") else "remembered"}
    return None


# ------------------------------------------------------------------ what the person reads
def ask(lang: str) -> tuple[str, list[str]]:
    """The question (before answering) and its taps."""
    if lang == "hindi":
        return ("यह query किस database के लिए है? SQL भाषा है, database नहीं, और जवाब (error codes, fix, syntax) database के हिसाब से बदलता है। "
                "चुनिए, फिर मैं उसी के लिए जाँचूँगा।"), TAPS["hi"]
    if lang == "hinglish":
        return ("Ye query kis database ki hai? SQL ek language hai, database nahi, aur jawab (error codes, fix, syntax) database ke hisaab se "
                "badalta hai. Chuno, phir main usi ke liye check karunga."), TAPS["hi"]
    return ("Which database is this for? SQL is the language, not the database, and the answer (error codes, fixes, syntax) "
            "depends on it. Pick one and I'll check it for that database."), TAPS["en"]


def detected_line(rec: dict, lang: str) -> tuple[str, list[str]]:
    """'Detected: PostgreSQL (from ::date)' plus one-tap corrections; '' when the person named or chose it."""
    if not rec or rec.get("how") not in ("detected",):
        return "", []
    eid = rec["id"]
    name = "MySQL / MariaDB" if eid == "mysql" else label(eid)
    why = rec.get("why") or ""
    others = [label(e) for e in ORDER if e != eid and not (eid == "mysql" and e == "mariadb")][:3]
    if lang == "hindi":
        line = f"पहचाना गया database: {name}" + (f" ({why} से)" if why else "") + "। गलत है तो नीचे सही database चुनिए।"
        taps = [f"यह {o} है" for o in others]
    elif lang == "hinglish":
        line = f"Database pehchana: {name}" + (f" ({why} se)" if why else "") + ". Galat hai to neeche sahi database chuno."
        taps = [f"Ye {o} hai" for o in others]
    else:
        line = f"Detected database: {name}" + (f" (from {why})" if why else "") + ". Not right? Tap the one you use."
        taps = [f"It's {o}" for o in others]
    return line, taps


def notes_line(eid: str, lang: str) -> str:
    """For SQL Server / Oracle / SQLite: which parts are general SQL reasoning and which are only notes."""
    if not notes_only(eid):
        return ""
    d = dialect(eid)
    notes = " ".join(d.get("notes", {}).values())
    if lang == "en":
        return (f"{d['name']}: DBaba has no lab-tested {d['name']} knowledge yet. The checks above are general SQL reasoning "
                f"(true on every database); anything said about MySQL/PostgreSQL behaviour doesn't apply. {d['name']} notes (from its "
                f"docs, not lab-tested): {notes}")
    return (f"{d['name']}: iska lab-tested knowledge abhi nahi hai. Upar ke checks general SQL reasoning hain (har database pe sach); "
            f"MySQL/PostgreSQL wali baatein yahan lagu nahi. {d['name']} notes (docs se, lab mein test nahi): {notes}")


def not_sure_line(lang: str) -> str:
    if lang == "en":
        return ("Database not known: where MySQL/MariaDB and PostgreSQL behave differently, each is shown and labelled. "
                "Tell me the database any time and I'll answer for that one only.")
    return ("Database pata nahi: jahan MySQL/MariaDB aur PostgreSQL alag chalte hain, dono label ke saath dikhaye hain. "
            "Database batao to sirf usi ka jawab dunga.")


def lang_of(text: str) -> str:
    from app.ml.normalize import detect_language

    return {"Hindi": "hindi", "Hinglish": "hinglish"}.get(detect_language(text or ""), "en")
