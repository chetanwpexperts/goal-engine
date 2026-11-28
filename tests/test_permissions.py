"""Permission dependencies deny by default and reuse the existing checks."""
import secrets
from contextlib import contextmanager

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.products.dbaba import database
from app.products.dbaba.local.server import COOKIE, SESSION_IDLE, make_app
from app.security.admin import ADMIN_COOKIE, _admin_token
from app.security.permissions import PERMISSIONS, require
from app.services.connector import api_key_allowed


def _routes(app, names=PERMISSIONS):
    for name in names:
        app.add_api_route("/permission/" + name, lambda: {"ok": True},
                          dependencies=[Depends(require(name))])


def test_unknown_permission():
    with pytest.raises(ValueError, match="Unknown permission"):
        require("typo")


def test_web_permissions(monkeypatch, fresh_db):
    app = FastAPI()
    app.state.permission_checks = {"api_key": api_key_allowed}

    @app.middleware("http")
    async def identity(request: Request, call_next):
        # Simulate the state that the existing identity middleware resolves.
        if request.headers.get("x-test-visitor") == "yes":
            request.state.visitor_id = "visitor"
        if request.headers.get("x-test-account") == "yes":
            request.state.user = object()
        return await call_next(request)

    _routes(app)
    monkeypatch.setenv("ADMIN_KEY", secrets.token_urlsafe(32))
    with contextmanager(get_db)() as db:
        _, key = database.create_connector(db, "permission-test", "Test")
    with TestClient(app) as client:
        assert client.get("/permission/public").status_code == 200
        for name in PERMISSIONS - {"public"}:
            assert client.get("/permission/" + name).status_code in (401, 404)
        assert client.get("/permission/visitor", headers={"x-test-visitor": "yes"}).status_code == 200
        assert client.get("/permission/account", headers={"x-test-account": "yes"}).status_code == 200
        assert client.get("/permission/account", headers={"x-test-visitor": "yes"}).status_code == 401
        client.cookies.set(ADMIN_COOKIE, "invalid")
        assert client.get("/permission/admin").status_code == 404
        client.cookies.set(ADMIN_COOKIE, _admin_token())
        assert client.get("/permission/admin").status_code == 200
        assert client.get("/permission/api_key", headers={"Authorization": "Bearer invalid"}).status_code == 401
        assert client.get("/permission/api_key", headers={"Authorization": "Bearer " + key}).status_code == 200


def test_local_permissions(tmp_path):
    import time

    token = secrets.token_urlsafe(32)
    app = make_app(str(tmp_path / "dbaba.json"), setup_token=token, announce=lambda _: None)
    _routes(app, {"public", "api_key", "local_screen", "local_setup"})
    key = app.state.cfg.new_api_key("Test")
    session = secrets.token_urlsafe(32)
    app.state.sessions[session] = time.time() + SESSION_IDLE
    base = "http://127.0.0.1:8765"
    with TestClient(app, base_url=base, client=("127.0.0.1", 50000)) as client:
        assert client.get("/permission/public").status_code == 200
        for name in ("api_key", "local_screen", "local_setup"):
            assert client.get("/permission/" + name).status_code == 401
        assert client.get("/permission/api_key", headers={"Authorization": "Bearer " + key}).status_code == 200
        assert client.get("/permission/local_screen", headers={"Authorization": "Bearer " + key}).status_code == 401
        assert client.get("/permission/local_setup", headers={"X-DBaba-Setup": token}).status_code == 200
        assert client.get("/permission/local_setup", headers={"X-DBaba-Setup": "invalid"}).status_code == 401
        client.cookies.set(COOKIE, session)
        assert client.get("/permission/local_screen").status_code == 200
        assert client.get("/permission/local_setup").status_code == 200
        assert client.get("/permission/api_key").status_code == 401
        app.state.sessions[session] = time.time() - 1
        assert client.get("/permission/local_screen").status_code == 401
    with TestClient(app, base_url=base, client=("192.0.2.1", 50000)) as remote:
        assert remote.get("/permission/local_setup", headers={"X-DBaba-Setup": token}).status_code == 401


def test_local_route_permission_alternatives(tmp_path):
    import time

    token = secrets.token_urlsafe(32)
    app = make_app(str(tmp_path / "dbaba.json"), setup_token=token, announce=lambda _: None)
    key = app.state.cfg.new_api_key("Test")
    session = secrets.token_urlsafe(32)
    app.state.sessions[session] = time.time() + SESSION_IDLE
    with TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/report").json() == {"error": "Log in first."}
        assert client.get("/api/state", headers={"X-DBaba-Setup": token}).status_code == 200
        assert client.get("/api/report", headers={"X-DBaba-Setup": token}).status_code == 401
        headers = {"Authorization": "Bearer " + key}
        assert client.get("/api/report", headers=headers).status_code == 200
        assert client.post("/api/keys", headers=headers, json={"name": "Test"}).status_code == 401
        client.cookies.set(COOKIE, session)
        assert client.get("/api/report").status_code == 200
        assert client.post("/api/keys", json={"name": "Test"}).status_code == 200
        client.cookies.clear()
        app.state.cfg.update(lambda d: d.__setitem__("setup_done", True))
        assert client.post("/api/setup/test", json={}).status_code == 409
