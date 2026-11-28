"""Shared goal ownership check."""

from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.models import Goal
from app.data import goals as storage


def own_goal(db: Session, goal_id: int, visitor: str) -> Goal:
    goal = storage.get_goal(db, goal_id)
    if not goal or goal.visitor_id != visitor:
        raise HTTPException(404, "Goal not found")
    return goal
