"""AI providers behind one small interface, so the model can be swapped any time."""

from typing import Protocol

import requests


class ProviderError(RuntimeError):
    pass


class Provider(Protocol):
    name: str

    def complete_json(self, system: str, user: str) -> str: ...


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str, timeout: int = 60, session: requests.Session | None = None,
                 thinking: str = "low"):
        self.api_key, self.model, self.timeout, self.thinking = api_key, model, timeout, thinking
        self.session = session or requests.Session()

    def complete_json(self, system: str, user: str) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
        }
        if self.thinking:
            # Newer Gemini models think before answering; "low" keeps replies fast.
            body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": self.thinking}
        response = self.session.post(url, json=body, headers={"x-goog-api-key": self.api_key}, timeout=self.timeout)
        if response.status_code != 200:
            raise ProviderError(f"gemini HTTP {response.status_code}: {response.text[:200]}")
        try:
            parts = response.json()["candidates"][0]["content"]["parts"]
            # skip "thought" parts; only the answer text is JSON
            return "".join(p.get("text", "") for p in parts if not p.get("thought"))
        except (KeyError, IndexError, ValueError) as exc:
            raise ProviderError(f"gemini returned no text: {exc}") from exc


class GroqProvider:
    name = "groq"

    def __init__(self, api_key: str, model: str, timeout: int = 40, session: requests.Session | None = None):
        self.api_key, self.model, self.timeout = api_key, model, timeout
        self.session = session or requests.Session()

    def complete_json(self, system: str, user: str) -> str:
        response = self.session.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json={
                "model": self.model,
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise ProviderError(f"groq HTTP {response.status_code}: {response.text[:200]}")
        try:
            return response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise ProviderError(f"groq returned no text: {exc}") from exc
