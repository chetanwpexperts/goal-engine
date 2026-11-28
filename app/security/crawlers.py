"""Reject requests identifying themselves as disallowed crawlers."""
from starlette.responses import JSONResponse


BLOCKED_AGENTS = (
    "GPTBot",
    "ChatGPT-User",
    "OAI-SearchBot",
    "ClaudeBot",
    "Claude-Web",
    "anthropic-ai",
    "Google-Extended",
    "CCBot",
    "PerplexityBot",
    "Bytespider",
    "Applebot-Extended",
    "meta-externalagent",
    "Amazonbot",
    "cohere-ai",
    "Diffbot",
    "ImagesiftBot",
    "Omgilibot",
    "YouBot",
)
_BLOCKED_LOWER = tuple(agent.lower() for agent in BLOCKED_AGENTS)


def robots_groups() -> str:
    return "".join(f"\nUser-agent: {agent}\nDisallow: /\n" for agent in BLOCKED_AGENTS)


def blocked(user_agent: str) -> bool:
    ua = user_agent.lower()
    return any(agent in ua for agent in _BLOCKED_LOWER)


class CrawlerMiddleware:
    """Check before routing, including static files and unknown paths."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] != "/robots.txt":
            if any(blocked(value.decode("latin-1")) for name, value in scope["headers"]
                   if name.lower() == b"user-agent"):
                response = JSONResponse({"error": "Forbidden"}, status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
