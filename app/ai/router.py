"""Try providers in order; accept only output that matches the schema."""

import json
import logging
import re

from pydantic import ValidationError

from app.ai.prompts import SYSTEM_PROMPT
from app.ai.providers import GeminiProvider, GroqProvider, Provider, ProviderError
from app.ai.schema import Interpretation
from app.config import Settings


logger = logging.getLogger(__name__)


class AIUnavailable(RuntimeError):
    pass


def _extract_json(text: str) -> dict:
    text = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fenced:
        text = fenced.group(1)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


class AIRouter:
    def __init__(self, providers: list[Provider]):
        self.providers = providers

    @classmethod
    def from_settings(cls, settings: Settings) -> "AIRouter":
        available = {
            "gemini": lambda: GeminiProvider(settings.gemini_api_key, settings.gemini_model, settings.ai_timeout_seconds)
            if settings.gemini_api_key else None,
            "groq": lambda: GroqProvider(settings.groq_api_key, settings.groq_model, settings.ai_timeout_seconds)
            if settings.groq_api_key else None,
        }
        providers = []
        for name in settings.ai_provider_order.split(","):
            factory = available.get(name.strip().lower())
            provider = factory() if factory else None
            if provider:
                providers.append(provider)
        return cls(providers)

    def interpret(self, user_prompt: str) -> tuple[Interpretation, str]:
        if not self.providers:
            raise AIUnavailable("No AI key is set (GEMINI_API_KEY or GROQ_API_KEY)")
        errors = []
        for provider in self.providers:
            try:
                raw = provider.complete_json(SYSTEM_PROMPT, user_prompt)
                return Interpretation.model_validate(_extract_json(raw)), provider.name
            except (ProviderError, ValidationError, ValueError) as exc:
                logger.warning("AI provider %s failed: %s", provider.name, exc)
                errors.append(f"{provider.name}: {str(exc)[:200]}")
            except Exception as exc:  # noqa: BLE001 - network errors etc., try the next provider
                logger.warning("AI provider %s error: %s", provider.name, exc)
                errors.append(f"{provider.name}: {type(exc).__name__}")
        raise AIUnavailable("; ".join(errors))
