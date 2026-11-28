"""Nightly learning feed route."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.services import learning_feed as service

from app.security.permissions import require

router = APIRouter()


@router.get("/api/learning/feed", dependencies=[Depends(require("api_key"))])
def learning_feed(key: str = "", db: Session = Depends(get_db)):
    return service.learning_feed(key, db)
