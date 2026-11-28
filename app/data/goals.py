"""Goal and message persistence."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import Goal, Message

RECENT_MESSAGES = 6


def active_goals(db: Session, visitor_id: str, limit: int | None = None) -> list[Goal]:
    query = (select(Goal).where(Goal.visitor_id == visitor_id, Goal.status == "active")
             .order_by(Goal.updated_at.desc(), Goal.id.desc()))
    if limit:
        query = query.limit(limit)
    return list(db.scalars(query))


def all_goals(db: Session, visitor_id: str) -> list[Goal]:
    return list(db.scalars(select(Goal).where(Goal.visitor_id == visitor_id)
                           .order_by(Goal.updated_at.desc(), Goal.id.desc())))


def recent_messages(db: Session, visitor_id: str) -> list[dict[str, str]]:
    rows = list(db.scalars(select(Message).where(Message.visitor_id == visitor_id)
                           .order_by(Message.id.desc()).limit(RECENT_MESSAGES)))
    return [{"role": m.role, "text": m.text[:500]} for m in reversed(rows)]


def focus_goal(db: Session, visitor_id: str, goal_id: int | None) -> Goal | None:
    """The goal the person is looking at, else their most recent active goal."""
    if goal_id:
        goal = db.get(Goal, goal_id)
        if goal and goal.visitor_id == visitor_id and goal.status == "active":
            return goal
    goals = active_goals(db, visitor_id, 1)
    return goals[0] if goals else None


def get_goal(db, goal_id):
    return db.get(Goal, goal_id)


def add(db, row):
    db.add(row)


def flush(db):
    db.flush()


def commit(db):
    db.commit()


def replace_last_reply(db, visitor_id, reply):
    last = db.scalars(select(Message).where(Message.visitor_id == visitor_id, Message.role == "assistant")
                      .order_by(Message.id.desc()).limit(1)).first()
    if last is not None:
        last.text = reply
        db.commit()
