"""Database report scheduling and delivery."""
import logging
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy.orm import Session
from app.agents import automation, database as dbagent
from app.agents.mailer import SmtpMailer
from app.domain.models import Agent, Goal, Message, dumps, loads, utcnow
from app.domain.schemas.db_jobs import DbReportIn
from app.services.agents import own_agent, _goal_hi, _agent_payload

# Composition root supplies the legacy mailer override at runtime.
mailer = SmtpMailer


def _run_db_report(db: Session, agent: Agent) -> None:
    from app.agents.spec import next_run
    from app.domain.models import Connector

    inputs = loads(agent.inputs, {}) or {}
    connector = db.get(Connector, inputs.get("connector_id") or 0)
    agent.next_run_at = next_run(inputs.get("schedule") or {"every": "day", "at": "09:00"}, utcnow())
    agent.last_run_at = utcnow()
    db.commit()
    if connector is not None and connector.status != "disconnected":
        dbagent.queue(db, connector, "export", {"sql": inputs.get("sql", "")}, agent_id=agent.id)


def _after_db_job(db: Session, job) -> None:
    """A scheduled database report finished: email the CSV to the verified owner."""
    if not job.agent_id or job.status != "done" or not job.file_path:
        return
    agent = db.get(Agent, job.agent_id)
    if agent is None or agent.recipe != "db_report":
        return
    owner = automation.owner_of(db, agent.visitor_id)
    if owner is None or not owner.verified_at:
        return
    inputs = loads(agent.inputs, {}) or {}
    title = inputs.get("title") or "Database report"
    try:
        with open(job.file_path, "rb") as fh:
            data = fh.read()
        if job.file_path.endswith(".gz"):
            import gzip

            data = gzip.decompress(data)
        rows = (loads(job.result, {}) or {}).get("row_count", "")
        files = [(f"{title[:40]}.csv", data, "text/csv")]
        text = f"{title}\n\nRows: {rows}\nThe CSV file is attached (opens in Excel)."
        try:                                        # S3: the same rows as a workbook with a live pivot and its chart
            from app.agents import sqlexcel
            from app.domain.models import Connector

            info = sqlexcel.for_job(job, db.get(Connector, job.connector_id))
            with open(info["path"], "rb") as fh:
                files.append((f"{title[:40]}.xlsx", fh.read(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
            text = f"{title}\n\nRows: {rows}\n{info['say']}\nAttached: the Excel workbook and the plain CSV."
        except Exception:  # noqa: BLE001 - the CSV still goes
            logging.getLogger(__name__).exception("Report workbook failed")
        mailer().send(owner.email, f"{title} · {utcnow():%d %b}", text, from_name="Goal Engine", attachments=files)
    except Exception:  # noqa: BLE001 - the next run will try again
        logging.getLogger(__name__).exception("Database report email failed")


def schedule_db_report(agent_id: int, body: DbReportIn, db: Session, visitor: str):
    agent = own_agent(db, agent_id, visitor)
    inputs = loads(agent.inputs, {}) or {}
    try:
        connector = dbagent.own_connector(db, inputs.get("connector_id") or 0, visitor)
        sql = dbagent.table_export_sql(body.table) if body.table else dbagent.read_only_sql(body.sql)
    except dbagent.DatabaseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    owner = automation.owner_of(db, visitor)
    if owner is None or not owner.verified_at:
        return JSONResponse({"error": "Verify your email first, so the report has somewhere to go"}, status_code=400)
    schedule = {"every": body.every if body.every in ("day", "week", "month") else "day", "at": body.at, "weekday": body.weekday, "day": 1}
    from app.agents.spec import next_run, schedule_text

    goal = db.get(Goal, agent.goal_id)
    hi = _goal_hi(goal)
    title = (body.title or (f"{body.table} report" if body.table else "Database report"))[:80]
    report = Agent(visitor_id=visitor, goal_id=agent.goal_id, recipe="db_report", status="active",
                   inputs=dumps({"connector_id": connector.id, "sql": sql, "schedule": schedule, "title": title}),
                   next_run_at=next_run(schedule, utcnow()))
    db.add(report)
    db.commit()
    reply = (f"{title}: {schedule_text(schedule, hi)} apne aap aapko email aayegi (CSV, Excel mein khulegi)." if hi
             else f"{title}: emailed to you {schedule_text(schedule, hi).lower()}, as a CSV that opens in Excel.")
    db.add(Message(visitor_id=visitor, goal_id=agent.goal_id, role="assistant", text=reply, relation="agent_run", provider="agent"))
    db.commit()
    return _agent_payload(db, agent, visitor, reply)
