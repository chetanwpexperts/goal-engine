from app.ai.providers import GeminiProvider, GroqProvider, ProviderError
from app.ai.router import AIRouter
from app.config import Settings


class Resp:
    def __init__(self, status, payload):
        self.status_code, self._payload, self.text = status, payload, str(payload)

    def json(self):
        return self._payload


class Session:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.resp


def test_gemini_request_and_parse():
    s = Session(Resp(200, {"candidates": [{"content": {"parts": [{"text": '{"a":1}'}]}}]}))
    out = GeminiProvider("KEY", "gemini-2.5-flash", session=s).complete_json("sys", "user")
    url, kw = s.calls[0]
    assert out == '{"a":1}'
    assert "gemini-2.5-flash:generateContent" in url
    assert kw["headers"]["x-goog-api-key"] == "KEY"  # key in header, never in the URL
    assert kw["json"]["generationConfig"]["responseMimeType"] == "application/json"


def test_groq_request_and_parse():
    s = Session(Resp(200, {"choices": [{"message": {"content": '{"a":1}'}}]}))
    out = GroqProvider("KEY", "llama", session=s).complete_json("sys", "user")
    assert out == '{"a":1}'
    assert s.calls[0][1]["json"]["response_format"] == {"type": "json_object"}


def test_http_error_raises_provider_error():
    s = Session(Resp(429, {"error": "quota"}))
    try:
        GeminiProvider("KEY", "m", session=s).complete_json("s", "u")
    except ProviderError as exc:
        assert "429" in str(exc)
    else:
        raise AssertionError("expected ProviderError")


def test_router_order_and_skips_missing_keys():
    r = AIRouter.from_settings(Settings(gemini_api_key="", groq_api_key="g", ai_provider_order="gemini,groq"))
    assert [p.name for p in r.providers] == ["groq"]
    r = AIRouter.from_settings(Settings(gemini_api_key="a", groq_api_key="g", ai_provider_order="groq,gemini"))
    assert [p.name for p in r.providers] == ["groq", "gemini"]
