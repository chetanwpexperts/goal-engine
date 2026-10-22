"""Running planned automations: preview first, owner verified, capped, logged.

Trust rules:
- Preview shows exactly who gets what before anything is sent.
- Nothing goes to customers until the owner has verified their own email.
- Caps per run and per day; the same person never gets the same agent's email twice in 20 hours.
- Every email is logged; replies go straight to the owner (Reply-To).
"""

import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.blocks import (RULE_FIELDS, BlockError, Table, fill, load_sheet, map_fields, parse_csv, select_rows,
                               summarize, valid_email)
from app.agents.mailer import Mailer, SmtpMailer
from app.agents.spec import describe, next_run, schedule_text, validate
from app.models import Agent, AgentRun, Goal, Message, OwnerContact, SentEmail, dumps, loads, utcnow


MAX_PER_RUN = 50
MAX_PER_DAY = 100
REPEAT_GAP = timedelta(hours=20)
CODE_TTL = timedelta(minutes=15)
FIELD_LABELS = {"name": "Name", "email": "Email", "phone": "Phone", "amount": "Amount", "due_date": "Due date",
                "appointment": "Appointment", "last_visit": "Last visit", "birthday": "Birthday",
                "visit_date": "Visit date", "status": "Status"}


class AutomationError(RuntimeError):
    pass


# ---------------------------------------------------------------- owner verification
def _secret() -> bytes:
    return (os.getenv("APP_SECRET") or os.getenv("DB_PASSWORD") or "goal-engine").encode()


def _hash(code: str) -> str:
    return hmac.new(_secret(), code.encode(), hashlib.sha256).hexdigest()


def owner_of(db: Session, visitor_id: str) -> OwnerContact | None:
    return db.scalar(select(OwnerContact).where(OwnerContact.visitor_id == visitor_id))


def request_code(db: Session, visitor_id: str, email: str, business: str, mailer: Mailer, hi: bool) -> None:
    email = (email or "").strip().lower()
    if not valid_email(email):
        raise AutomationError("Please enter a valid email address")
    owner = owner_of(db, visitor_id)
    now = utcnow()
    if owner is None:
        owner = OwnerContact(visitor_id=visitor_id, email=email, codes_today=0, attempts=0)
        db.add(owner)
    if owner.code_sent_at and now - owner.code_sent_at < timedelta(seconds=60):
        raise AutomationError("Please wait a minute before asking for another code")
    if owner.code_sent_at and owner.code_sent_at.date() == now.date() and (owner.codes_today or 0) >= 5:
        raise AutomationError("Too many codes today. Please try again tomorrow")
    code = f"{secrets.randbelow(1_000_000):06d}"
    owner.codes_today = (owner.codes_today or 0) + 1 if owner.code_sent_at and owner.code_sent_at.date() == now.date() else 1
    owner.email, owner.business_name = email, (business or "").strip()[:120] or None
    owner.code_hash, owner.code_sent_at, owner.attempts, owner.verified_at = _hash(code), now, 0, None
    db.commit()
    body = (f"Aapka Goal Engine code: {code}\n\nYe 15 minute tak valid hai. Agar aapne ye nahi maanga, to is email ko ignore karein."
            if hi else f"Your Goal Engine code: {code}\n\nIt's valid for 15 minutes. If you didn't ask for it, ignore this email.")
    mailer.send(email, f"Your code: {code}", body, from_name="Goal Engine")


def verify_code(db: Session, visitor_id: str, code: str) -> OwnerContact:
    owner = owner_of(db, visitor_id)
    if owner is None or not owner.code_hash:
        raise AutomationError("Ask for a code first")
    if (owner.attempts or 0) >= 5:
        raise AutomationError("Too many wrong tries. Ask for a new code")
    if not owner.code_sent_at or utcnow() - owner.code_sent_at > CODE_TTL:
        raise AutomationError("The code has expired. Ask for a new one")
    if not hmac.compare_digest(owner.code_hash, _hash((code or "").strip())):
        owner.attempts = (owner.attempts or 0) + 1
        db.commit()
        raise AutomationError("That code isn't right")
    owner.verified_at, owner.code_hash = utcnow(), None
    db.commit()
    return owner


# ---------------------------------------------------------------- agent helpers
def spec_of(agent: Agent) -> dict:
    return (loads(agent.inputs, {}) or {}).get("spec") or {}


def create_agent(db: Session, goal: Goal, spec: dict) -> Agent:
    agent = Agent(visitor_id=goal.visitor_id, goal_id=goal.id, recipe="automation", status="proposed",
                  inputs=dumps({"spec": spec, "sheet_url": "", "csv_text": ""}))
    db.add(agent)
    db.flush()
    return agent


def plan_reply(spec: dict, hi: bool) -> str:
    lines = [(f"Main ye agent bana raha hoon: {spec['title']}" if hi else f"Here's the agent I'll set up: {spec['title']}")]
    lines += [f"{i}. {s['text']}" for i, s in enumerate(describe(spec, hi), 1)]
    needs = [FIELD_LABELS[f] for f in RULE_FIELDS.get(spec["rule"], []) + (["name", "email"] if spec["kind"] == "reminder" else [])]
    cols = ", ".join(dict.fromkeys(needs))
    lines.append(("Bas apni sheet ka link chahiye" + (f" (columns: {cols})" if cols else "") + ". Pehle preview dikhaunga, phir aap Start dabaoge."
                  if hi else "I just need your sheet link" + (f" (columns: {cols})" if cols else "") + ". I'll show a preview first; then you tap Start."))
    return "\n".join(lines)


def automation_dict(db: Session, agent: Agent, hi: bool) -> dict:
    inputs = loads(agent.inputs, {}) or {}
    spec = inputs.get("spec") or {}
    owner = owner_of(db, agent.visitor_id)
    runs = list(db.scalars(select(AgentRun).where(AgentRun.agent_id == agent.id).order_by(AgentRun.id.desc()).limit(5)))
    return {
        "id": agent.id, "lang": "hi" if hi else "en", "recipe": "automation", "automation": True,
        "name": spec.get("title", "Automation"), "status": agent.status, "access": "none", "available": True,
        "kind": spec.get("kind"), "schedule": schedule_text(spec.get("schedule", {}), hi),
        "blocks": describe(spec, hi) if spec else [],
        "message": spec.get("message_hi" if hi else "message_en", ""), "subject": spec.get("subject", ""),
        "needs_columns": [FIELD_LABELS[f] for f in dict.fromkeys(RULE_FIELDS.get(spec.get("rule"), [])
                          + (["name", "email"] if spec.get("kind") == "reminder" else []))],
        "inputs": [{"key": "sheet_url", "kind": "url", "label": "Google Sheet link (Anyone with the link can view)" if not hi
                    else "Google Sheet ka link (Anyone with the link: Viewer)", "required": False, "multiline": False,
                    "value": inputs.get("sheet_url", "")},
                   {"key": "csv_text", "kind": "text", "label": "…or paste the rows (CSV)" if not hi else "…ya rows yahan paste karo (CSV)",
                    "required": False, "multiline": True, "value": inputs.get("csv_text", "")}],
        "ready": bool(inputs.get("sheet_url") or inputs.get("csv_text")),
        "needs_owner": spec.get("send_to") == "row_email" or spec.get("kind") == "report",
        "owner": {"email": owner.email if owner else "", "verified": bool(owner and owner.verified_at),
                  "business": owner.business_name if owner else ""},
        "next_run_at": agent.next_run_at.isoformat() if agent.next_run_at else None,
        "runs": [{"id": r.id, "status": r.status, "summary": r.summary, "result": loads(r.result, None),
                  "started_at": r.started_at.isoformat(), "finished_at": r.finished_at.isoformat() if r.finished_at else None}
                 for r in runs],
    }


def _table(inputs: dict, fetcher=None) -> Table:
    if inputs.get("sheet_url"):
        return load_sheet(inputs["sheet_url"], fetcher) if fetcher else load_sheet(inputs["sheet_url"])
    if inputs.get("csv_text"):
        return parse_csv(inputs["csv_text"])
    raise BlockError("Add your Google Sheet link, or paste the rows")


def sent_today(db: Session, visitor_id: str) -> int:
    start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return db.scalar(select(func.count(SentEmail.id)).where(SentEmail.visitor_id == visitor_id, SentEmail.status == "sent",
                                                            SentEmail.sent_at >= start)) or 0


def execute(db: Session, agent: Agent, hi: bool, *, preview: bool, mailer: Mailer | None = None,
            fetcher=None, today=None) -> dict:
    inputs = loads(agent.inputs, {}) or {}
    spec = inputs.get("spec") or {}
    problems = validate(spec)
    if problems:
        raise AutomationError("This plan isn't valid: " + "; ".join(problems))
    table = _table(inputs, fetcher)
    fields = map_fields(table.columns, spec["fields"])
    needed = list(dict.fromkeys(RULE_FIELDS.get(spec["rule"], []) + (["email"] if spec["send_to"] == "row_email" else [])))
    missing = [FIELD_LABELS[f] for f in needed if not fields.get(f)]
    if missing:
        raise BlockError(("Sheet mein ye column nahi mile: " if hi else "Couldn't find these columns in your sheet: ")
                         + ", ".join(missing) + (". Aapke columns: " if hi else ". Your columns: ") + ", ".join(table.columns))
    rows = select_rows(table, fields, spec["rule"], spec.get("days", 3), today=today)
    owner = owner_of(db, agent.visitor_id)
    sender_name = (owner.business_name if owner and owner.business_name else "") or "Goal Engine"
    result = {"ok": True, "preview": preview, "total_rows": len(table.rows), "matched": len(rows),
              "mapping": {FIELD_LABELS.get(f, f): c for f, c in fields.items() if c}}

    if spec["kind"] == "report":
        body = summarize(table, rows, fields, hi)
        subject = fill(spec.get("subject") or spec["title"], {}, fields)
        result["report"] = {"subject": subject, "body": body}
        if not preview:
            if not owner or not owner.verified_at:
                raise AutomationError("Verify your email first, so the report has somewhere to go")
            (mailer or SmtpMailer()).send(owner.email, f"{subject} · {utcnow():%d %b}", body, from_name="Goal Engine")
            result["sent"] = 1
        return result

    footer = ("\n\n—\nYe message {b} ki taraf se hai. Jawab dene ke liye is email ka reply karein." if hi
              else "\n\n—\nThis message is from {b}. Reply to this email to reach them directly.")
    template = spec["message_hi"] if hi else spec["message_en"]
    email_col = fields["email"]
    messages, skipped = [], []
    for row in rows:
        to = (row.get(email_col) or "").strip()
        if not valid_email(to):
            skipped.append({"row": row.get(fields.get("name") or "", "") or to, "reason": "no valid email"})
            continue
        messages.append({"to": to, "subject": fill(spec["subject"], row, fields)[:150],
                         "body": fill(template, row, fields) + footer.format(b=sender_name)})
    result["skipped"] = skipped
    if preview:
        result["messages"] = messages[:MAX_PER_RUN]
        result["over_cap"] = max(0, len(messages) - MAX_PER_RUN)
        return result

    if not owner or not owner.verified_at:
        raise AutomationError("Verify your email first. Nothing goes to your customers until you do")
    mailer = mailer or SmtpMailer()
    room = max(0, min(MAX_PER_RUN, MAX_PER_DAY - sent_today(db, agent.visitor_id)))
    recent = {e for e in db.scalars(select(SentEmail.to_email).where(SentEmail.agent_id == agent.id, SentEmail.status == "sent",
                                                                     SentEmail.sent_at >= utcnow() - REPEAT_GAP))}
    sent, failed, held = [], [], 0
    for m in messages:
        if m["to"].lower() in recent:
            continue
        if len(sent) >= room:
            held += 1
            continue
        record = SentEmail(visitor_id=agent.visitor_id, agent_id=agent.id, to_email=m["to"].lower(), subject=m["subject"], status="sent")
        try:
            mailer.send(m["to"], m["subject"], m["body"], reply_to=owner.email, from_name=sender_name)
            sent.append(m["to"])
        except Exception as exc:  # noqa: BLE001 - one bad address must not stop the rest
            record.status, record.error = "failed", str(exc)[:300]
            failed.append(m["to"])
        db.add(record)
        recent.add(m["to"].lower())
    db.commit()
    result.update({"sent": len(sent), "failed": failed, "held_for_cap": held, "sent_to": sent[:MAX_PER_RUN]})
    summary = (f"{spec['title']}: {len(sent)} bheje, {len(failed)} fail, {len(skipped)} bina email." if hi
               else f"{spec['title']}: {len(sent)} sent, {len(failed)} failed, {len(skipped)} without an email.")
    if sent or failed:
        try:
            mailer.send(owner.email, summary[:150], summary + ("\n\nBheje gaye: " if hi else "\n\nSent to: ") + ", ".join(sent[:50]),
                        from_name="Goal Engine")
        except Exception:  # noqa: BLE001 - the summary is a courtesy
            pass
    return result


def summary_line(result: dict, hi: bool) -> str:
    if result.get("preview"):
        if "report" in result:
            return ("Preview taiyar: report mein " if hi else "Preview ready: the report has ") + f"{result['matched']} " + ("rows." if not hi else "rows hain.")
        n = len(result.get("messages", []))
        return (f"Preview: aaj {n} logo ko message jayega (sheet mein {result['total_rows']} rows). Card mein dekho kisko kya jayega."
                if hi else f"Preview: {n} people would get a message today ({result['total_rows']} rows in the sheet). See exactly what in the card.")
    if "report" in result:
        return "Report aapko email kar di." if hi else "Report emailed to you."
    return (f"{result.get('sent', 0)} message bheje, {len(result.get('failed', []))} fail." if hi
            else f"Sent {result.get('sent', 0)} messages, {len(result.get('failed', []))} failed.")


def run(db: Session, agent: Agent, hi: bool, *, preview: bool, mailer: Mailer | None = None, fetcher=None,
        scheduled: bool = False, today=None) -> AgentRun:
    run_row = AgentRun(agent_id=agent.id, status="running")
    db.add(run_row)
    db.commit()
    try:
        result = execute(db, agent, hi, preview=preview, mailer=mailer, fetcher=fetcher, today=today)
        run_row.status = "done"
        run_row.summary = summary_line(result, hi)
    except (BlockError, AutomationError) as exc:
        result = {"ok": False, "error": str(exc), "preview": preview}
        run_row.status, run_row.summary = "failed", str(exc)
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "error": f"Agent failed: {type(exc).__name__}", "preview": preview}
        run_row.status, run_row.summary = "failed", result["error"]
    run_row.result, run_row.finished_at = dumps(result), utcnow()
    agent.last_run_at = run_row.finished_at
    if scheduled and agent.status == "active":
        agent.next_run_at = next_run(spec_of(agent)["schedule"], utcnow())
        goal = db.get(Goal, agent.goal_id)
        if goal is not None:
            db.add(Message(visitor_id=agent.visitor_id, goal_id=goal.id, role="assistant", text=run_row.summary or "",
                           relation="agent_run", provider="agent"))
    db.commit()
    return run_row


def activate(db: Session, agent: Agent) -> None:
    owner = owner_of(db, agent.visitor_id)
    if not owner or not owner.verified_at:
        raise AutomationError("Verify your email first")
    inputs = loads(agent.inputs, {}) or {}
    if not (inputs.get("sheet_url") or inputs.get("csv_text")):
        raise AutomationError("Add your sheet first")
    agent.status = "active"
    agent.next_run_at = next_run(spec_of(agent)["schedule"], utcnow())
    db.commit()
