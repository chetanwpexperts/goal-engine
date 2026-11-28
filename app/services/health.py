"""Health response for the website."""

from app.ai.router import AIRouter
from app.core.config import get_settings


def health(router: AIRouter) -> dict:
    from app.engine.ml.slots import SPECS

    if not get_settings().expose_internals:
        return {"ok": True}                                  # how the engine works isn't anyone's business
    return {"ok": True, "engine": get_settings().engine_mode, "problem_types": len(SPECS) - 1,
            "ai_providers": [p.name for p in router.providers]}
