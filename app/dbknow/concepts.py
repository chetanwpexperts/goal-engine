"""Basic and concept questions about databases ("what is a JOIN", "group by kya hota hai", "DELETE vs TRUNCATE",
"how to delete duplicate rows in mysql"), answered from the shared concepts pack app/dbknow/core/concepts.json.

How a question is read, without AI and without fixed sentences:
1. its SHAPE: what is / kya hota hai / kya hai / X vs Y / difference between / how to / kaise / when to use / why ...
2. the CONCEPT, picked by its words: every concept lists term groups; a group counts when all its terms are in the
   question, and the most specific match wins (more and longer terms: 'left join' beats 'join', 'delete duplicate'
   beats 'duplicate').
3. what it must NOT be: a pasted query, a pasted error, a complaint about the person's own query or database ('my query is
   slow', 'this query gives an error') belongs to the query checker / the error diagnoser, never here.
Every example query in the pack was run on MariaDB and PostgreSQL (tests/practice/concepts/lab.py) and its result is kept
with it. Engine differences: the database named in the question (or shown by its words) gets its own note; otherwise
MySQL/MariaDB and PostgreSQL are both shown, briefly.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

PACK = Path(__file__).resolve().parent / "core" / "concepts.json"

ENGINES = {                                   # how people name each database, and how its note is labelled
    "mysql": (r"\bmy\s?sql\b|माय\s?एसक्यूएल", "MySQL"),
    "mariadb": (r"\bmaria\s?db\b", "MariaDB"),
    "postgresql": (r"\bpostgre(s|sql)?\b|\bpg\b|\bpsql\b|पोस्टग्रे", "PostgreSQL"),
    "sqlserver": (r"\bsql\s?server\b|\bmssql\b|\bt-?sql\b|\bms\s?sql\b", "SQL Server"),
    "oracle": (r"\boracle\b|\bpl/?sql\b", "Oracle"),
    "sqlite": (r"\bsqlite3?\b", "SQLite"),
}
NOTES_ONLY = {"sqlserver", "oracle", "sqlite"}   # no lab-tested knowledge yet: their notes are general guidance

SHAPES = [                                     # (shape, pattern) in the order they are tried
    ("compare", r"^which (one )?is (better|faster|safer)\b|\b(better|behtar|faster|tez)\b.*\b(ya|or)\b|बेहतर.*या|"
                r"\bsame (hai|hain|h|he|hote|hota)\b|\bsame as\b|एक ही (है|हैं)|\bmein\b.*\bya\b.*\bmein\b|में .*या.* में"),
    ("compare", r"\b(ya|or)\b.*\b(kaunsa|konsa|kaun sa|kaunsi|which)\b.*\b(better|behtar|faster|tez|sahi|accha|acha)\b"),
    ("compare", r"\b(vs\.?|versus|difference(s)? (between|in|of)|differ(ent|ence)?\b.*\b(from|and)|compared? (to|with)|"
                r"(farak|fark|farq|antar|difference) (kya|hai|h|batao|bataiye|samjhao)|(mein|me|main) (kya )?(farak|fark|farq|antar|difference)|"
                r"(farak|fark|farq|antar)\b|or\b.*\bwhich (one|is better))|अंतर|फ़र्क|फर्क|फरक|बनाम|में क्या अंतर"),
    ("howto", r"^(how (to|do i|do we|do you|can i|can we|should i|would i)|how .* (find|get|remove|delete|write|show|list|select|count)|"
              r"ways? to|best way to|query (to|for)|sql (to|for)|give me (a |the )?(query|sql)|write (a |the )?(query|sql))\b|"
              r"\b(kaise|kese|kaisey|kis tarah|kaise kare|kaise karein|kaise karte|kaise nikale|kaise nikaalein|kaise hataye|kaise likhe)\b|"
              r"कैसे|किस तरह"),
    ("define", r"^(what(\s+(is|are|'s|r)|s)|whats|define|definition of|meaning of|explain|tell me about|what do you mean by|"
               r"what does .* mean|describe|concept of|basics of|intro(duction)? to|teach me|samjhao|samjhaiye|batao)\b|"
               r"\b(kya (hota|hoti|hote|hai|h|he|hain|cheez|chiz)|kya hai|kise kehte|kisko kehte|matlab kya|ka matlab|ki definition|"
               r"kya kaam (aata|karta)|meaning)\b|\bmeans\?|क्या (होता|होती|होते|है|हैं)|किसे कहते|मतलब|समझाइए|समझाओ|बताइए"),
    ("define", r"^(what does .* (do|mean|stand for|return)|how (does|do) .* work|what happens (when|if))\b|"
               r"\b(kya (karta|karti|karte|deta|deti) (hai|h|hain))\b|क्या करता है|क्या करती है|कैसे काम करत"),
    ("why", r"^why\b|\b(kyun|kyu|kyon|kiu)\b|क्यों|क्यूँ"),
    ("when", r"^should (i|we|you|one)\b|\bshould (a|an|the|this)? ?\w+ (go|be put|be written)\b|\b(karna|karni|karne|lagana|likhna) chahiye\b|"
             r"\bchahiye kya\b"),
    ("howto", r"^can (i|we|you|one)\b|^is (it|there a way) (possible )?to\b"),
    ("define", r"^(does|do|is|are|will)\b.*\b(matter|matters|same|different|allowed|possible|a database|a language|faster|slower|"
               r"better|quicker)\b|"
               r"^(is|are) \w+( \w+)? (a|an)\b|\b(hai|h|hain|karta hai|karti hai) kya\s*$|(है|हैं|करता है) क्या\s*$|मायने रखता"),
    ("define", r"^[\w\s-]{2,40}\b(in|on|for|with|mein|me|में) (sql|mysql|maria ?db|postgre\w*|sql server|oracle|sqlite)$"),
    ("puzzle", r"\b(gives?|returns?|shows?|giving|returning) (random|different|wrong|duplicate|extra|null|zero|0|same)\b|"
               r"\b(gives?|returns?|shows?|comes?) .*\b(twice|two times|double)\b"),
    ("task", r"^(find|remove|delete|get rid of|show|list|count|get|write|make|combine|merge|replace|join|sort|search)\b"),
    ("when", r"\b(when (to|should i|do we|do i|would you) use|where (to|should i) use|why (use|do we use|should i use|would i use|is .* used)|"
             r"what is .* used for|use of|uses of|kab use|kab (karte|karna|lagate|lagana)|kyun use|kyu use|kis liye)\b|कब (इस्तेमाल|उपयोग)|क्यों (इस्तेमाल|उपयोग)"),
]
SHAPE_RE = [(n, re.compile(p, re.I)) for n, p in SHAPES]

# a question about the person's own query, error or database is real work for the checker / diagnoser, not a concept
OWN_WORK = re.compile(
    r"\b(my|this|that|the above|our|mera|meri|mere|ye|yeh|iss|isme|ismein|upar wali) (sql )?(query|queries|statement|code|error)\b|"
    r"\b(error|errors|fail(s|ed|ing)?|failure|slow(ly|er)?|not working|doesn.?t work|isn.?t working|stuck|crash\w*|denied|refused|"
    r"time(d)? ?out|exception|warning|broken|hang(s|ing)?|nahi chal\w*|kaam nahi|atak\w*|dheem\w*|dhire)\b|"
    r"\b(sqlstate|errno|err(or)? ?code)\b|\b[0-9]{4,5}\b|एरर|त्रुटि|धीम|काम नहीं|चल नहीं|"
    r"(ये|यह|इस|इसे|इसको|मेरी|मेरा|हमारी|ऊपर वाली)\s*(sql\s*)?(क्वेरी|query|स्टेटमेंट)", re.I)
# another field's file (a spreadsheet's duplicates are the office agent's work)
OTHER_FIELD = re.compile(r"\b(excel|spreadsheets?|sheets?|xlsx?|csv|vlookup|xlookup|pivot table|word file|docx|google sheets?)\b|एक्सेल|शीट", re.I)
# the words that make an unknown 'what is X' a question about databases (so it is answered honestly here, not elsewhere)
DB_MARK = re.compile(r"\b(sql|mysql|maria ?db|postgre\w*|query|queries|table|tables|column|columns|rows?|database|databases|"
                     r"db|dbms|rdbms|schema|clause|keyword|statement|index(es)?|key|keys|join\w*|engine)\b|"
                     r"क्वेरी|टेबल|डेटाबेस|एसक्यूएल", re.I)
PERSONAL = re.compile(r"\b(my|mera|meri|mere|our|hamara|hamari|apna|apni|connect\w*|backup|restore|export|import|install\w*|"
                      r"size|version|password|login|user\w*|server|host|port|kitna|kitni|kitne)\b|मेरा|मेरी|हमारा", re.I)


LETTER = r"[\wऀ-ॿ]"                     # a letter, Devanagari vowel signs included (\w alone misses them)


def _plain_len(term: str) -> int:
    """How specific a term is: the length of its plain words (lookarounds and regex signs don't count; for a|b, the first)."""
    out, depth, i = [], 0, 0
    while i < len(term):
        if term.startswith("(?", i) and not term.startswith("(?:", i):
            depth, i = depth + 1, i + 2
            continue
        ch = term[i]
        if depth:
            depth += {"(": 1, ")": -1}.get(ch, 0)
        elif ch == "\\":
            i += 2
            continue
        elif ch.isalnum() or ch in " |" or "ऀ" <= ch <= "ॿ":
            out.append(ch)
        i += 1
    return len("".join(out).split("|")[0].strip())


@lru_cache(maxsize=1)
def pack() -> dict:
    data = json.loads(PACK.read_text(encoding="utf-8"))
    for c in data["concepts"]:
        c["_groups"] = [[re.compile(rf"(?<!{LETTER})" + t.replace(r"\w", LETTER) + rf"(?!{LETTER})", re.I) for t in g]
                        for g in c["terms"]]
        c["_weight"] = [sum(_plain_len(t) for t in g) + 6 * (len(g) - 1) for g in c["terms"]]
    return data


def concepts() -> list[dict]:
    return pack()["concepts"]


def by_id(cid: str) -> dict | None:
    return next((c for c in concepts() if c["id"] == cid), None)


def _clean(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"[\"“”‘’`]", " ", t)
    t = re.sub(r"[?!.,;:()]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def shape(text: str) -> str | None:
    t = _clean(text)
    for name, rx in SHAPE_RE:
        if rx.search(t):
            return name
    return None


def engine_named(text: str) -> str | None:
    """The database the question names ('in mysql', 'postgres mein'), if any."""
    for eid, (rx, _label) in ENGINES.items():
        if re.search(rx, text or "", re.I):
            return eid
    return None


def _score(c: dict, t: str) -> int:
    best = 0
    for g, w in zip(c["_groups"], c["_weight"]):
        if all(rx.search(t) for rx in g):
            best = max(best, w)
    return best


def _is_work(text: str) -> bool:
    """A pasted query, a pasted error, or a complaint about their own query/database: the checker's or diagnoser's job."""
    from app.agents.core import query_in

    if query_in(text) or re.search(r"\bselect\b[\s\S]+\bfrom\b|\b(update|delete\s+from|insert\s+into)\b\s+[`\"]?\w+[`\"]?\s+(set|where|values)\b",
                                   text or "", re.I):
        return True
    try:
        from app.dbknow import reasoning

        facts = reasoning.evidence(text)
        # a question that only NAMES a setting ('what is ONLY_FULL_GROUP_BY') is a concept; a pasted message or code is work
        if facts and not all(re.fullmatch(r"[A-Za-z_]+", f.get("proof", "")) for f in facts.values()):
            return True
    except Exception:  # noqa: BLE001
        pass
    return bool(OWN_WORK.search(text or "") or OTHER_FIELD.search(text or ""))


def match(text: str) -> dict | None:
    """{'concept': <concept>, 'shape': ..., 'engine': ...} for a basic/concept question; {'concept': None, ...} for a
    database concept question we don't know yet; None when it isn't a concept question at all."""
    if not text or len(text) > 300 or "\n" in text.strip():
        return None
    sh = shape(text)
    if sh is None or _is_work(text):
        return None
    t = _clean(text)
    scored = sorted(((_score(c, t), c) for c in concepts()), key=lambda x: -x[0])
    top, best = scored[0]
    if top and sh == "compare":                 # 'X vs Y': a comparison concept that names both, when there is one
        pair = [(s, c) for s, c in scored if s and c.get("kind") == "compare"]
        if pair and pair[0][0] >= top * 0.6:
            top, best = pair[0]
    if top and sh == "task" and best.get("kind") != "howto":
        return None                             # 'list all indexes': a job on their database, not a lesson
    if top and sh == "puzzle" and PERSONAL.search(t):
        return None                             # 'my join returns duplicates': their own problem, for the checker
    if top:
        return {"concept": best, "shape": sh, "engine": engine_named(text)}
    if sh in ("define", "compare", "when") and DB_MARK.search(t) and not PERSONAL.search(t):
        return {"concept": None, "shape": sh, "engine": engine_named(text)}
    return None


def nearest(text: str, k: int = 3) -> list[dict]:
    """The known concepts closest to an unknown question (shared words), for 'did you mean'."""
    words = {w for w in re.findall(r"[a-z]{3,}", _clean(text).lower())} - {"what", "the", "and", "sql", "mysql", "hai", "kya", "hota",
                                                                         "between", "difference", "how", "does", "mean", "query"}
    scored = []
    for c in concepts():
        bag = set(re.findall(r"[a-z]{3,}", (c["title"]["en"] + " " + " ".join(c["phrasings"]["en"])).lower()))
        n = len(words & bag)
        if n:
            scored.append((n, c))
    scored.sort(key=lambda x: -x[0])
    out = [c for _, c in scored[:k]]
    for cid in ("select", "join", "group_by", "index"):
        if len(out) >= k:
            break
        c = by_id(cid)
        if c and c not in out:
            out.append(c)
    return out


def _lang(text: str) -> str:
    from app.ml.normalize import detect_language

    return {"Hindi": "hindi", "Hinglish": "hinglish"}.get(detect_language(text), "en")


LABELS = {
    "en": {"example": "Example", "result": "Result", "mistake": "Common mistake", "engines": "Database differences", "preview":
           "First look at what will change (preview)", "change": "Then the change (run it inside a transaction; keep a backup)",
           "notes": "general SQL guidance, not lab-tested on", "related": "Related", "unknown":
           "I don't have a checked answer for that concept yet, so I won't guess. I've noted the question so DBaba learns it.",
           "near": "Closest things I can explain now:", "for": "for", "lab": "run on MariaDB and PostgreSQL"},
    "hinglish": {"example": "Example", "result": "Result", "mistake": "Aam galti", "engines": "Database ka farak", "preview":
                 "Pehle dekho kya badlega (preview)", "change": "Phir badlaav (transaction mein chalao; backup rakho)",
                 "notes": "general SQL samajh, lab mein test nahi hua:", "related": "Ye bhi dekho", "unknown":
                 "Is concept ka jaancha hua jawab abhi mere paas nahi hai, isliye andaza nahi lagaunga. Sawaal note kar liya hai taaki DBaba ise seekhe.",
                 "near": "Abhi ye samjha sakta hoon:", "for": "", "lab": "MariaDB aur PostgreSQL pe chalaya"},
    "hindi": {"example": "उदाहरण", "result": "नतीजा", "mistake": "आम गलती", "engines": "डेटाबेस का फ़र्क", "preview":
              "पहले देखिए क्या बदलेगा (preview)", "change": "फिर बदलाव (transaction में चलाइए; backup रखिए)",
              "notes": "सामान्य SQL समझ, लैब में जाँचा नहीं गया:", "related": "यह भी देखें", "unknown":
              "इस concept का जाँचा हुआ जवाब अभी मेरे पास नहीं है, इसलिए अंदाज़ा नहीं लगाऊँगा। सवाल नोट कर लिया है ताकि DBaba इसे सीखे।",
              "near": "अभी ये समझा सकता हूँ:", "for": "", "lab": "MariaDB और PostgreSQL पर चलाया"},
}


def _sql_for(ex: dict, engine: str | None) -> list[tuple[str, str]]:
    """[(label, sql)] of the example for the named engine, or each engine's version when they differ."""
    sql = ex.get("sql") or {}
    if "all" in sql:
        return [("", sql["all"])]
    fam = {"mysql": "mysql", "mariadb": "mysql", "postgresql": "postgresql"}.get(engine or "")
    if fam and fam in sql:
        return [("", sql[fam])]
    return [({"mysql": "MySQL / MariaDB", "postgresql": "PostgreSQL"}.get(k, k), v) for k, v in sql.items()]


def _result(rows: list, cols: list) -> str:
    if not rows:
        return "(no rows)"
    shown = [" | ".join(cols)] if cols else []
    shown += [" | ".join("NULL" if v is None else str(v) for v in r) for r in rows[:6]]
    if len(rows) > 6:
        shown.append(f"... ({len(rows)} rows)")
    return "\n".join(shown)


def answer(m: dict, text: str) -> tuple[str, list[str]]:
    """The reply (in the language asked) and up to 3 taps for related concepts."""
    lang = _lang(text)
    L = LABELS[lang]
    c = m["concept"]
    eng = m.get("engine")
    if c is None:
        near = nearest(text)
        lines = [L["unknown"], "", L["near"]] + [f"- {x['title'][lang] if lang in x['title'] else x['title']['en']}" for x in near]
        return "\n".join(lines), [x["phrasings"][lang][0] for x in near]
    title = c["title"].get(lang) or c["title"]["en"]
    out = [f"{title}", "", c["answer"][lang]]
    for ex in c.get("examples", []):
        out.append("")
        head = ex.get("label", {}).get(lang) or ex.get("label", {}).get("en") or ""
        if ex.get("preview"):
            out.append(f"{L['preview']}:")
            for lab, q in _sql_for({"sql": ex["preview"]}, eng):
                out.append((f"-- {lab}\n" if lab else "") + q)
            if ex.get("preview_result") is not None:
                out.append(f"{L['result']}:\n{_result(ex['preview_result'], ex.get('preview_columns') or [])}")
            out.append("")
            out.append(f"{L['change']}:")
        else:
            out.append(f"{L['example']}{(' — ' + head) if head else ''} ({L['lab']}):")
        for lab, q in _sql_for(ex, eng):
            out.append((f"-- {lab}\n" if lab else "") + q)
        if ex.get("result") is not None:
            out.append(f"{L['result']}:\n{_result(ex['result'], ex.get('columns') or [])}")
        elif isinstance(ex.get("changed"), int) and ex.get("preview"):
            out.append({"en": f"Rows changed: {ex['changed']}", "hinglish": f"Badli rows: {ex['changed']}",
                        "hindi": f"बदली rows: {ex['changed']}"}[lang])
    out += ["", f"{L['mistake']}: {c['mistake'][lang]}"]
    notes = c.get("engines") or {}
    if notes:
        if eng and eng in notes:
            label = ENGINES[eng][1]
            pre = f"{label} ({L['notes']} {label})" if eng in NOTES_ONLY and lang == "en" else (
                f"{label} ({L['notes']})" if eng in NOTES_ONLY else label)
            out += ["", f"{L['engines']} — {pre}: {notes[eng]}"]
        elif eng in NOTES_ONLY:
            out += ["", f"{L['engines']} — {ENGINES[eng][1]}: " + (
                "I have no lab-tested notes for it yet; the answer above is general SQL." if lang == "en" else
                "iske lab-tested notes abhi nahi hain; upar ka jawab general SQL hai." if lang == "hinglish" else
                "इसके लैब में जाँचे notes अभी नहीं हैं; ऊपर का जवाब सामान्य SQL है।")]
        else:
            keys = [k for k in ("mysql", "mariadb", "postgresql") if k in notes]
            if eng:
                keys = [eng] + [k for k in keys if k != eng] if eng in notes else keys
            out += ["", f"{L['engines']}:"] + [f"- {ENGINES[k][1]}: {notes[k]}" for k in keys]
    rel = [by_id(r) for r in c.get("related", [])]
    taps = [r["phrasings"][lang][0] for r in rel if r][:3]
    return "\n".join(out).strip(), taps
