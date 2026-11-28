"""Authorized nightly learning feed."""

import hmac
import os

from fastapi import HTTPException
from sqlalchemy.orm import Session


def learning_feed(key: str, db: Session):
    """For the nightly learning only (LEARN_KEY): scrubbed sentences people asked us to learn."""
    from app import learning

    want = os.getenv("LEARN_KEY", "")
    if len(want) < 16 or not hmac.compare_digest(key, want):
        raise HTTPException(404, "Not Found")
    from app.mind import packs, predict

    # plus the question paths real people take (labels only), so the nightly run can tune the starting chains
    return {"requests": learning.feed(db), "paths": {b: predict.top_paths(db, b) for b in packs.all_packs()}}
