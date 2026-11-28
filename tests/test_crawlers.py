"""Crawler policy for the public website and the self-hosted screen."""
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.products.dbaba.local.server import make_app
from app.security.crawlers import BLOCKED_AGENTS


EXPECTED_AGENTS = (
    "GPTBot", "ChatGPT-User", "OAI-SearchBot", "ClaudeBot", "Claude-Web",
    "anthropic-ai", "Google-Extended", "CCBot", "PerplexityBot", "Bytespider",
    "Applebot-Extended", "meta-externalagent", "Amazonbot", "cohere-ai",
    "Diffbot", "ImagesiftBot", "Omgilibot", "YouBot",
)


def directives(response):
    return {part.strip().lower() for part in response.headers["X-Robots-Tag"].split(",")}


def test_robots_open_and_closed(client, monkeypatch):
    assert set(BLOCKED_AGENTS) == set(EXPECTED_AGENTS)
    for mode in ("open", "closed"):
        monkeypatch.setenv("SITE_MODE", mode)
        response = client.get("/robots.txt", headers={"User-Agent": "GPTBot/1.0"})
        assert response.status_code == 200
        assert {"noai", "noimageai"} <= directives(response)
        if mode == "closed":
            assert response.text == "User-agent: *\nDisallow: /\n"
        else:
            assert response.text.startswith("User-agent: *\nAllow: /\n")
            for agent in EXPECTED_AGENTS:
                assert f"User-agent: {agent}\nDisallow: /\n" in response.text


@pytest.mark.parametrize("agent", EXPECTED_AGENTS)
def test_blocked_agents(client, agent):
    for path in ("/", "/api/health", "/static/index.html", "/missing-path", "/admin"):
        response = client.get(path, headers={"User-Agent": f"Mozilla/5.0 ({agent.swapcase()}/1.0)"})
        assert response.status_code == 403
        assert {"noai", "noimageai"} <= directives(response)
    assert client.post("/api/message", headers={"User-Agent": agent}, json={}).status_code == 403
    assert client.get("/robots.txt", headers={"User-Agent": agent}).status_code == 200


def test_normal_requests_and_existing_directives(client, monkeypatch):
    monkeypatch.setenv("SITE_MODE", "open")
    for agent in ("Mozilla/5.0", "Googlebot", "Bingbot", ""):
        for path in ("/", "/static/index.html", "/api/health", "/robots.txt"):
            response = client.get(path, headers={"User-Agent": agent})
            assert response.status_code == 200
            assert {"noai", "noimageai"} <= directives(response)
    assert {"noindex", "nofollow", "noai", "noimageai"} <= directives(client.get("/console"))
    response = client.get("/missing-path")
    assert response.status_code == 404
    assert {"noai", "noimageai"} <= directives(response)
    monkeypatch.setenv("SITE_MODE", "closed")
    response = client.get("/")
    assert response.status_code == 200
    assert {"noindex", "nofollow", "noarchive", "noai", "noimageai"} <= directives(response)
    assert client.get("/", headers={"User-Agent": "GPTBot"}).status_code == 403


def test_local_crawler_policy(tmp_path):
    local = make_app(str(tmp_path / "dbaba.json"), announce=lambda _: None)
    with TestClient(local, base_url="http://127.0.0.1:8765") as client:
        for path in ("/", "/api/health", "/local/local.js", "/missing-path"):
            response = client.get(path, headers={"User-Agent": "GPTBot"})
            assert response.status_code == 403
            assert {"noai", "noimageai"} <= directives(response)
        response = client.get("/api/health")
        assert response.status_code == 200
        assert {"noai", "noimageai"} <= directives(response)
        # Local DBaba has no robots route; the middleware must not intercept it.
        response = client.get("/robots.txt", headers={"User-Agent": "GPTBot"})
        assert response.status_code == 404
        assert {"noai", "noimageai"} <= directives(response)


class RobotsMeta(HTMLParser):
    def __init__(self):
        super().__init__()
        self.directives = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and attrs.get("name", "").lower() == "robots":
            self.directives.update(part.strip().lower() for part in attrs.get("content", "").split(","))


def test_all_static_html_robots_metadata():
    root = Path(__file__).resolve().parents[1] / "app" / "static"
    pages = sorted(root.rglob("*.html"))
    assert pages
    for page in pages:
        parser = RobotsMeta()
        parser.feed(page.read_text(encoding="utf-8"))
        assert {"noai", "noimageai"} <= parser.directives, page.name
        if page.name in {"admin.html", "console.html", "coming-soon.html"}:
            assert {"noindex", "nofollow"} <= parser.directives, page.name
