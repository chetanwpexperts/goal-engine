"""The admin panel's data: who is using Goal Engine and how much, the enquiries, and what DBaba knows.

Every endpoint is the owner's only (the admin cookie, app/main.py); anyone else gets 404, as if nothing were here.
Numbers are counted from what Goal Engine keeps anyway; the private DBaba workspace adds only per-day counts (app/usage.py).
"""

import glob
import json
import re
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import enquiry, usage
from app.db import get_db
from app.models import (Agent, DbConnectLog, Enquiry, ExpertRequest, LearnedPhrase, LearnMiss, LearnRequest, Message,
                        User, UserSession, utcnow)

router = APIRouter(prefix="/api/admin")
ROOT = Path(__file__).resolve().parent.parent
DBKNOW = Path(__file__).resolve().parent / "dbknow"
OFFICEKNOW = Path(__file__).resolve().parent / "officeknow"

AREAS = {"dbaba": ("DBaba (databases)", {"database", "db_report", "sql_review", "db_automation"}),
         "sheetu": ("Sheetu (Excel and Word)", {"office"}),
         "website": ("Website checker", {"website_check", "website_watch"}),
         "automation": ("Automation agents", {"automation"})}
DAYS = 30


def _admin(request: Request) -> None:
    from app.main import _is_admin

    if not _is_admin(request):
        raise HTTPException(404, "Not Found")


def _area(recipe: str) -> str:
    return next((k for k, (_, recipes) in AREAS.items() if recipe in recipes), "other")


def _days(n: int = DAYS) -> list[str]:
    today = utcnow().date()
    return [(today - timedelta(days=k)).strftime("%Y-%m-%d") for k in range(n - 1, -1, -1)]


def _per_day(stamps, n: int = DAYS) -> list[int]:
    c = Counter(s.strftime("%Y-%m-%d") for s in stamps if s is not None)
    return [c.get(d, 0) for d in _days(n)]


# ---------------------------------------------------------------- overview
@router.get("/overview")
def overview(request: Request, db: Session = Depends(get_db)):
    _admin(request)
    since = utcnow() - timedelta(days=DAYS)
    week = utcnow() - timedelta(days=7)
    days = _days()

    msgs = db.execute(select(Message.visitor_id, Message.created_at)
                      .where(Message.role == "user", Message.created_at >= since)).all()
    by_day: dict[str, set] = defaultdict(set)
    for v, at in msgs:
        by_day[at.strftime("%Y-%m-%d")].add(v)
    people = [len(by_day.get(d, ())) for d in days]
    messages = _per_day([at for _, at in msgs])

    agents = db.execute(select(Agent.recipe, Agent.created_at)).all()
    areas = {k: {"label": label, "total": 0, "month": 0, "week": 0} for k, (label, _) in AREAS.items()}
    for recipe, at in agents:
        a = areas.get(_area(recipe))
        if a is None:
            continue
        a["total"] += 1
        a["month"] += at is not None and at >= since
        a["week"] += at is not None and at >= week

    users_total = db.scalar(select(func.count()).select_from(User)) or 0
    verified = db.scalar(select(func.count()).select_from(User).where(User.email_verified.is_(True))) or 0
    signups = _per_day(db.scalars(select(User.created_at).where(User.created_at >= since)).all())
    active = db.scalar(select(func.count(func.distinct(UserSession.user_id))).where(UserSession.last_seen_at >= week)) or 0

    conns = db.execute(select(DbConnectLog.at, DbConnectLog.ok).where(DbConnectLog.at >= since)).all()
    enq = enquiry.counts(db)
    experts_new = db.scalar(select(func.count()).select_from(ExpertRequest).where(ExpertRequest.status == "new")) or 0

    return {
        "days": days,
        "people": people, "messages": messages,
        "people_week": len({v for v, at in msgs if at >= week}), "people_month": len({v for v, _ in msgs}),
        "accounts": {"total": users_total, "verified": verified, "active_week": active, "signups": signups},
        "areas": list(areas.values()),
        "workspace": {k: usage.series(db, k) for k in ("workspace_ask", "workspace_run", "workspace_connect")},
        "connections": {"ok": sum(1 for _, ok in conns if ok), "failed": sum(1 for _, ok in conns if not ok)},
        "enquiries": {"new": enq.get("new", 0), "total": sum(enq.values()), "per_day": usage.series(db, "enquiry")},
        "experts_new": experts_new,
        "learning": {"to_learn": db.scalar(select(func.count()).select_from(LearnRequest).where(LearnRequest.status == "new")) or 0,
                     "said_wrong": db.scalar(select(func.count()).select_from(LearnMiss).where(LearnMiss.status == "new")) or 0},
    }


# ---------------------------------------------------------------- people with accounts
@router.get("/users")
def users(request: Request, db: Session = Depends(get_db)):
    _admin(request)
    rows = db.scalars(select(User).order_by(User.created_at.desc()).limit(500)).all()
    vids = [u.visitor_id for u in rows]
    ids = [u.id for u in rows]
    seen = dict(db.execute(select(UserSession.user_id, func.max(UserSession.last_seen_at))
                           .where(UserSession.user_id.in_(ids)).group_by(UserSession.user_id)).all()) if ids else {}
    said = dict(db.execute(select(Message.visitor_id, func.count()).where(Message.visitor_id.in_(vids), Message.role == "user")
                           .group_by(Message.visitor_id)).all()) if vids else {}
    used: dict[str, Counter] = defaultdict(Counter)
    if vids:
        for v, recipe in db.execute(select(Agent.visitor_id, Agent.recipe).where(Agent.visitor_id.in_(vids))).all():
            used[v][_area(recipe)] += 1
    conns = dict(db.execute(select(DbConnectLog.user_id, func.count()).where(DbConnectLog.user_id.in_(ids), DbConnectLog.ok.is_(True))
                            .group_by(DbConnectLog.user_id)).all()) if ids else {}
    out = []
    for u in rows:
        last = max([t for t in (seen.get(u.id), u.last_login_at) if t is not None], default=None)
        out.append({"id": u.id, "name": u.full_name, "username": u.username, "email": u.email, "phone": u.phone,
                    "location": u.location, "why": u.why or "", "verified": bool(u.email_verified),
                    "joined": u.created_at.isoformat() if u.created_at else None,
                    "last_seen": last.isoformat() if last else None, "messages": int(said.get(u.visitor_id, 0)),
                    "areas": {AREAS[k][0] if k in AREAS else "Other": n for k, n in used[u.visitor_id].items()},
                    "connections": int(conns.get(u.id, 0))})
    return {"users": out}


# ---------------------------------------------------------------- enquiries
class EnquiryUpdate(BaseModel):
    status: str | None = None
    note: str | None = None


@router.get("/enquiries")
def enquiries(request: Request, status: str = "", db: Session = Depends(get_db)):
    _admin(request)
    q = select(Enquiry).order_by(Enquiry.created_at.desc()).limit(500)
    if status:
        q = q.where(Enquiry.status == status)
    return {"enquiries": [enquiry.as_dict(e) for e in db.scalars(q)], "counts": enquiry.counts(db),
            "statuses": list(enquiry.STATUSES)}


@router.post("/enquiries/{eid}")
def enquiry_update(eid: int, body: EnquiryUpdate, request: Request, db: Session = Depends(get_db)):
    _admin(request)
    e = db.get(Enquiry, eid)
    if e is None or (body.status is not None and body.status not in enquiry.STATUSES):
        raise HTTPException(404, "Not Found")
    if body.status is not None:
        e.status = body.status
    if body.note is not None:
        e.note = body.note.strip()[:4000]
    db.commit()
    return enquiry.as_dict(e)


@router.delete("/enquiries/{eid}")
def enquiry_delete(eid: int, request: Request, db: Session = Depends(get_db)):
    """When the person asks to be forgotten."""
    _admin(request)
    e = db.get(Enquiry, eid)
    if e is None:
        raise HTTPException(404, "Not Found")
    db.delete(e)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- what DBaba knows
def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _size(x) -> int:
    return len(x) if isinstance(x, (list, dict)) else 0


def curriculum() -> dict:
    """The nights of docs/dbaba-curriculum.md: which are done, and the night log's lines."""
    try:
        text = (ROOT / "docs" / "dbaba-curriculum.md").read_text(encoding="utf-8")
    except OSError:
        return {"nights": [], "done": 0, "total": 0}
    nights = [{"n": int(n), "done": x == "x", "title": t.strip().rstrip(".")}
              for x, n, t in re.findall(r"^- \[( |x)\] \*\*(\d+)\. ([^*]+)\*\*", text, re.M)]
    log = {int(n): d for n, d in re.findall(r"^- \*\*Night (\d+) \(([^)]+)\)", text, re.M)}
    for night in nights:
        night["date"] = log.get(night["n"], "")
    return {"nights": nights, "done": sum(n["done"] for n in nights), "total": len(nights)}


def knowledge() -> dict:
    core = DBKNOW / "core"
    concepts = _load(core / "concepts.json") or {}
    items = concepts.get("concepts") or []
    phrasings = sum(len(v) for c in items if isinstance(c, dict) for v in (c.get("phrasings") or {}).values()
                    if isinstance(v, list))
    qf = _load(core / "query_fundamentals.json") or {}
    fund = _load(core / "fundamentals.json") or {}
    engines = []
    for f in sorted(glob.glob(str(DBKNOW / "dialects" / "*.json"))):
        d = _load(Path(f)) or {}
        rules = sum(_size(d.get(k)) for k in ("query_rules", "schema_rules"))
        engines.append({"name": d.get("name") or Path(f).stem, "tested": not d.get("notes_only"),
                        "error_codes": _size(d.get("codes")), "messages": _size(d.get("messages")), "fixes": _size(d.get("fix")),
                        "query_rules": rules, "live_checks": _size(d.get("live")), "notes": _size(d.get("notes"))})
    checkers = sorted(Path(p).stem for p in glob.glob(str(ROOT / "app" / "agents" / "sql*.py")))
    practice = sorted(glob.glob(str(ROOT / "tests" / "practice" / "test_night*.py")))
    office = {name: _size(_load(OFFICEKNOW / f"{name}.json")) for name in ("issues", "formulas", "fundamentals")}
    return {
        "dbaba": {"concepts": len(items), "phrasings": phrasings,
                  "query_fundamentals": _size(qf.get("fundamentals")), "server_causes": _size(fund.get("causes")),
                  "known_issues": _size(_load(DBKNOW / "issues.json")), "engines": engines, "checkers": checkers,
                  "practice_nights": len(practice)},
        "sheetu": office,
        "curriculum": curriculum(),
    }


@router.get("/knowledge")
def knowledge_view(request: Request, db: Session = Depends(get_db)):
    _admin(request)
    misses = db.scalars(select(LearnMiss).where(LearnMiss.status == "new")
                        .order_by(LearnMiss.count.desc(), LearnMiss.last_seen.desc()).limit(100)).all()
    asked = db.scalars(select(LearnRequest).where(LearnRequest.status == "new")
                       .order_by(LearnRequest.count.desc(), LearnRequest.last_seen.desc()).limit(100)).all()
    learned = db.execute(select(LearnedPhrase.brain, LearnedPhrase.status, func.count())
                         .group_by(LearnedPhrase.brain, LearnedPhrase.status)).all()
    return {**knowledge(),
            "said_wrong": [{"id": m.id, "agent": m.brain, "said": m.said, "detail": m.detail, "answer": m.answer,
                            "count": m.count, "last_seen": m.last_seen.isoformat()} for m in misses],
            "asked": [{"id": r.id, "agent": r.brain, "text": r.text, "guess": r.guess, "count": r.count,
                       "last_seen": r.last_seen.isoformat()} for r in asked],
            "learned": [{"agent": b, "status": s, "count": n} for b, s, n in learned]}


class MissUpdate(BaseModel):
    status: str


@router.post("/knowledge/miss/{mid}")
def miss_update(mid: int, body: MissUpdate, request: Request, db: Session = Depends(get_db)):
    _admin(request)
    m = db.get(LearnMiss, mid)
    if m is None or body.status not in ("new", "learned", "ignored"):
        raise HTTPException(404, "Not Found")
    m.status = body.status
    db.commit()
    return {"ok": True}


class TryIn(BaseModel):
    text: str = ""


@router.post("/knowledge/try")
def try_dbaba(body: TryIn, request: Request):
    """Test box: DBaba's answer to anything, exactly as the workspace would give it (no database connected). Not kept."""
    from app import console

    _admin(request)
    text = (body.text or "").strip()[:30000]
    if not text:
        raise HTTPException(400, "Type something")
    blocks, engine = console.answer(text, None)
    return {"blocks": blocks, "engine": engine}
