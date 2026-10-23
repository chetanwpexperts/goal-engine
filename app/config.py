"""All settings come from .env (never from code)."""

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel
from sqlalchemy.engine import URL


load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


class Settings(BaseModel):
    app_name: str = os.getenv("APP_NAME", "Goal Engine")

    @property
    def expose_internals(self) -> bool:
        """How the brain read a message (intent, confidence, interpretation) stays on the server; tests switch this on."""
        return os.getenv("EXPOSE_INTERNALS", "") == "1"

    # Database: DATABASE_URL wins (used by tests); otherwise MySQL from the parts.
    database_url: str = os.getenv("DATABASE_URL", "")
    db_host: str = os.getenv("DB_HOST", "127.0.0.1")
    db_port: int = _int("DB_PORT", 3306)
    db_name: str = os.getenv("DB_NAME", "goal_engine")
    db_user: str = os.getenv("DB_USER", "goal_engine")
    db_password: str = os.getenv("DB_PASSWORD", "")

    # "ml" = own machine-learning engine (no external AI). "ai" = Gemini/Groq.
    engine_mode: str = os.getenv("ENGINE_MODE", "ml").strip().lower()

    # AI providers, tried in this order. Empty key = provider skipped.
    ai_provider_order: str = os.getenv("AI_PROVIDER_ORDER", "gemini,groq")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    gemini_thinking: str = os.getenv("GEMINI_THINKING", "low")   # low = fast; empty = model default
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    ai_timeout_seconds: int = _int("AI_TIMEOUT_SECONDS", 60)

    # Safety limits so a public demo can never run up a bill or be abused.
    ai_daily_cap: int = _int("AI_DAILY_CAP", 400)              # all visitors together
    visitor_daily_cap: int = _int("VISITOR_DAILY_CAP", 25)     # one visitor
    max_message_chars: int = _int("MAX_MESSAGE_CHARS", 1500)

    cookie_secure: bool = os.getenv("COOKIE_SECURE", "false").lower() == "true"

    def sqlalchemy_url(self) -> str | URL:
        if self.database_url:
            return self.database_url
        return URL.create(
            "mysql+pymysql",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
            query={"charset": "utf8mb4"},
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
