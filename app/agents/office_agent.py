"""The Microsoft agent: the person gives a file, the agent finds what's wrong and fixes it (a copy), or compares two.

Files go to data/office/ in chunks (resumable, up to 1.5 GB), work runs in the background one big file at a
time, and the privacy clean-up deletes everything after an hour. No AI: app/officeknow (what's wrong) +
app/office (doing it).
"""

import os
import re
import secrets
import shutil
import threading
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import office
from app.models import Agent, Goal, OfficeFile, dumps, loads, utcnow

DATA = Path(__file__).resolve().parents[2] / "data" / "office"
MAX_SIZE = 1536 * 1024 * 1024
CHUNK_MAX = 8 * 1024 * 1024
FILE_TTL = timedelta(hours=1)
BIG = 50 * 1024 * 1024
OPEN_FILES_PER_VISITOR = 6
_big_lane = threading.Semaphore(1)          # one big file at a time, so the VPS stays responsive
FILE_WORDS = re.compile(r"\b(excel|xlsx|xls|csv|spreadsheet|sheet|sheets|workbook|vlookup|xlookup|pivot|formula|formule|"
                        r"word file|docx|powerpoint|pptx|outlook|onedrive|eksel|shit|phormula)\b|#(REF!|N/A|VALUE!|DIV/0!|NAME\?)", re.I)
ALLOWED = (".xlsx", ".xlsm", ".csv", ".txt")


class OfficeAgentError(Exception):
    pass


# ------------------------------------------------------------------ chat side
def wants_office(goal: Goal | None, text: str) -> bool:
    if goal is None:
        return False
    from app.agents.database import DB_WORDS
    from app.officeknow import brain

    if DB_WORDS.search(text or ""):
        return False                                  # "database se excel chahiye" is the database agent's export
    rec = brain.recognize(text)
    if rec["confidence"] >= 0.99:
        return True                                   # an Excel error value like #REF!
    return bool(FILE_WORDS.search(text or "")) and rec["confidence"] >= 0.2


def goal_agent(db: Session, goal: Goal) -> Agent | None:
    return db.scalar(select(Agent).where(Agent.goal_id == goal.id, Agent.recipe == "office").order_by(Agent.id.desc()))


def create_agent(db: Session, goal: Goal) -> Agent:
    agent = Agent(visitor_id=goal.visitor_id, goal_id=goal.id, recipe="office", status="needs_input", inputs=dumps({}))
    db.add(agent)
    db.flush()
    return agent


def plan_reply(rec: dict, hi: bool) -> str:
    from app.officeknow import brain

    seen = brain.describe(rec, hi) if rec.get("confidence", 0) >= 0.35 else ""
    lead = (f"Samjha: {seen}. " if hi else f"Understood: {seen}. ") if seen else ""
    if rec.get("kind") == "formula" and seen:
        return lead + ("Formula card mein hai. File daaloge to main khud laga dunga." if hi
                       else "The formula is on the card. Give me the file and I'll put it in for you.")
    return lead + ("File card mein daalo. Main khud check karke theek kar dunga, aur aapko fixed copy milegi." if hi
                   else "Drop the file on the card. I'll check it myself, fix it, and give you a fixed copy.")


def office_dict(db: Session, agent: Agent, hi: bool) -> dict:
    inputs = loads(agent.inputs, {}) or {}
    files = list(db.scalars(select(OfficeFile).where(OfficeFile.agent_id == agent.id, OfficeFile.status != "removed")
                            .order_by(OfficeFile.id.desc()).limit(6)))
    return {"id": agent.id, "lang": "hi" if hi else "en", "recipe": "office", "office": True, "status": agent.status,
            "name": "Excel / file ka kaam" if hi else "Excel and file work", "access": "upload", "available": True,
            "blocks": [], "runs": [], "brain": _brain_view(inputs.get("brain"), hi), "formula": _formula_view(inputs.get("brain"), hi),
            "files": [file_dict(f) for f in files]}


def _brain_view(rec, hi):
    if not rec or not rec.get("kind"):
        return None
    from app.officeknow import brain

    return {**rec, "text": brain.describe(rec, hi)}


def _formula_view(rec, hi):
    if not rec or rec.get("kind") != "formula":
        return None
    from app import officeknow

    f = next((x for x in officeknow.load("formulas") if x["id"] == rec["id"]), None)
    if not f:
        return None
    return {"title": f["title_hi" if hi else "title_en"], "explain": f["explain_hi" if hi else "explain_en"],
            "excel": f["templates"].get("excel365", ""), "sheets": f["templates"].get("google_sheets", ""), "needs": f["needs"]}


# ------------------------------------------------------------------ files
def file_dict(f: OfficeFile) -> dict:
    return {"id": f.token, "name": f.name, "size": f.size, "received": f.received, "status": f.status, "task": f.task,
            "report": loads(f.report, None), "result": loads(f.result, None), "error": f.error,
            "download": bool(f.result_path and os.path.exists(f.result_path)), "result_name": f.result_name}


def start_upload(db: Session, visitor_id: str, agent_id: int | None, name: str, size: int) -> OfficeFile:
    name = os.path.basename(name or "file.xlsx")[:150]
    if not name.lower().endswith(ALLOWED):
        raise OfficeAgentError("Excel (.xlsx) ya CSV file daalo. Purani .xls ho to Excel mein Save As .xlsx karke daalo.")
    if not 0 < size <= MAX_SIZE:
        raise OfficeAgentError("File 1.5 GB tak ki ho sakti hai.")
    DATA.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(DATA).free < size * 3 + 2 * 1024**3:
        raise OfficeAgentError("Server abhi itni badi file nahi le sakta. Thodi der baad try karo.")
    open_now = db.scalars(select(OfficeFile).where(OfficeFile.visitor_id == visitor_id, OfficeFile.status.not_in(("removed",)))).all()
    if len(open_now) >= OPEN_FILES_PER_VISITOR:
        raise OfficeAgentError("Ek saath 6 files tak. Purani files 1 ghante mein apne aap hat jaati hain.")
    token = secrets.token_urlsafe(24)
    f = OfficeFile(token=token, visitor_id=visitor_id, agent_id=agent_id, name=name, size=size,
                   path=str(DATA / f"{token}{os.path.splitext(name)[1].lower()}"))
    db.add(f)
    db.commit()
    open(f.path, "wb").close()
    return f


def own_file(db: Session, token: str, visitor_id: str) -> OfficeFile:
    f = db.scalar(select(OfficeFile).where(OfficeFile.token == token))
    if not f or f.visitor_id != visitor_id or f.status == "removed":
        raise OfficeAgentError("File nahi mili (1 ghante baad files apne aap hat jaati hain).")
    return f


def add_chunk(db: Session, f: OfficeFile, offset: int, data: bytes) -> OfficeFile:
    if f.status != "uploading":
        raise OfficeAgentError("Upload pehle hi poora ho chuka hai.")
    if offset != f.received:
        return f                                     # the browser resumes from f.received
    if len(data) > CHUNK_MAX or f.received + len(data) > f.size:
        raise OfficeAgentError("Galat hissa aaya. Dobara upload karo.")
    with open(f.path, "r+b") as out:
        out.seek(offset)
        out.write(data)
    f.received += len(data)
    db.commit()
    return f


def finish_upload(db: Session, f: OfficeFile) -> OfficeFile:
    if f.received != f.size:
        raise OfficeAgentError("File poori nahi aayi. Upload dobara chalu karo, wahin se aage badhega.")
    try:
        office.kind_of(f.path)
    except office.OfficeError as exc:
        f.status, f.error = "error", str(exc)
        db.commit()
        return f
    return _start(db, f, "scan", {})


def start_fix(db: Session, f: OfficeFile, ops: list[str]) -> OfficeFile:
    ops = [o for o in ops if o in office.FIX_OPS]
    if f.status not in ("ready", "error") or not ops:
        raise OfficeAgentError("Pehle check poora hone do.")
    return _start(db, f, "fix", {"ops": ops})


def start_compare(db: Session, a: OfficeFile, b: OfficeFile, key: str = "") -> OfficeFile:
    if a.status not in ("ready", "error") or b.status not in ("ready", "error"):
        raise OfficeAgentError("Dono files ka upload aur check poora hone do.")
    return _start(db, a, "compare", {"other": b.id, "key": key})


def _start(db: Session, f: OfficeFile, task: str, args: dict) -> OfficeFile:
    f.status, f.task, f.error, f.args = "queued", task, None, dumps(args)
    db.commit()
    threading.Thread(target=_work, args=(f.id, task, args), daemon=True).start()
    return f


def _work(file_id: int, task: str, args: dict) -> None:
    from app.db import _SessionLocal, get_engine

    get_engine()
    with _SessionLocal() as db:
        f = db.get(OfficeFile, file_id)
        big = f.size > BIG or task == "compare"
        lane = _big_lane if big else None
        if lane:
            lane.acquire()
        try:
            f.status = "working"
            db.commit()
            if task == "scan":
                f.report = dumps(office.scan(f.path))
            elif task == "fix":
                root, ext = os.path.splitext(f.path)
                out = f"{root}.fixed{ext}"
                f.result = dumps({"task": "fix", **office.fix(f.path, out, args["ops"])})
                f.result_path, f.result_name = out, _named(f.name, "fixed")
            elif task == "compare":
                other = db.get(OfficeFile, args["other"])
                out = f.path + ".compare.xlsx"
                summary = office.compare(f.path, other.path, out, key=args.get("key", ""))
                f.result = dumps({"task": "compare", "other": other.name, **summary})
                f.result_path, f.result_name = out, _named(f.name, "compare", ".xlsx")
            f.status = "ready"
        except office.OfficeError as exc:
            f.status, f.error = "error", str(exc)
        except Exception as exc:  # noqa: BLE001 - a broken file must not crash the worker
            f.status, f.error = "error", f"File padhi nahi ja saki ({type(exc).__name__}). Excel mein khol kar Save As .xlsx karke dobara daalo."
        finally:
            if lane:
                lane.release()
            db.commit()


def _named(name: str, suffix: str, ext: str = "") -> str:
    base, e = os.path.splitext(name)
    return f"{base} ({suffix}){ext or e}"


def remove_file(f: OfficeFile) -> None:
    for p in (f.path, f.result_path):
        try:
            if p:
                os.remove(p)
        except OSError:
            pass
    f.path = f.result_path = None
    f.report = f.result = None
    f.status = "removed"


def maintain(db: Session, now=None) -> int:
    now = now or utcnow()
    n = 0
    for f in db.scalars(select(OfficeFile).where(OfficeFile.status != "removed", OfficeFile.created_at < now - FILE_TTL)):
        if f.status in ("queued", "working") and f.created_at > now - FILE_TTL * 3:
            continue                                  # still working on a very big file
        remove_file(f)
        n += 1
    for f in db.scalars(select(OfficeFile).where(OfficeFile.status == "removed", OfficeFile.created_at < now - timedelta(days=1))):
        db.delete(f)
    db.commit()
    return n


_resume_lock = None


def _resume_owner() -> bool:
    """Only one server process resumes work: the first to take this lock keeps it for its whole life."""
    global _resume_lock
    if _resume_lock is not None:
        return True
    import fcntl

    DATA.mkdir(parents=True, exist_ok=True)
    fh = open(DATA / ".resume.lock", "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return False
    _resume_lock = fh
    return True


def resume_interrupted(boot) -> int:
    """After a restart (an update, a crash), carry on with work that was running, in one process only."""
    from sqlalchemy import update

    from app.db import _SessionLocal, get_engine

    if not _resume_owner():
        return 0
    get_engine()
    n = 0
    with _SessionLocal() as db:
        ids = list(db.scalars(select(OfficeFile.id).where(OfficeFile.status.in_(("queued", "working")), OfficeFile.updated_at < boot)))
        for fid in ids:
            claimed = db.execute(update(OfficeFile).where(OfficeFile.id == fid, OfficeFile.status.in_(("queued", "working")),
                                                          OfficeFile.updated_at < boot).values(status="queued", updated_at=boot)).rowcount
            db.commit()
            if claimed:
                f = db.get(OfficeFile, fid)
                threading.Thread(target=_work, args=(f.id, f.task or "scan", loads(f.args, {}) or {}), daemon=True).start()
                n += 1
    return n
