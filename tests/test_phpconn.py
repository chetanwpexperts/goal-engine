"""PHP file connector, tested for real: PHP's built-in server plays the user's WordPress hosting."""

import shutil
import socket
import subprocess
import time

import pytest

from app.agents import direct, phpconn
from tests.test_ml import say


def _up(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not shutil.which("php") or not _up(3306), reason="needs php and a local MySQL with shop_db")


class Site:
    """The site folder, plus its address."""

    def __init__(self, path, base):
        self.path, self.base = path, base

    def __truediv__(self, name):
        return self.path / name


@pytest.fixture()
def wp_site(tmp_path):
    (tmp_path / "wp-config.php").write_text("<?php\ndefine( 'DB_NAME', 'shop_db' );\ndefine( 'DB_USER', 'shopapp' );\n"
                                            "define( 'DB_PASSWORD', 'app pass' );\ndefine( 'DB_HOST', '127.0.0.1' );\n")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    proc = subprocess.Popen(["php", "-S", f"127.0.0.1:{port}", "-t", str(tmp_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        if _up(port):
            break
        time.sleep(0.1)
    yield Site(tmp_path, f"http://127.0.0.1:{port}")
    proc.terminate()
    proc.wait(5)


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("PHP_CONNECTOR_ALLOW_HTTP", "1")
    monkeypatch.setenv("DIRECT_DB_ALLOW_PRIVATE", "1")
    monkeypatch.setenv("DIRECT_DB_SYNC", "1")
    direct._attempts.clear()


def test_php_file_end_to_end(client, ml_mode, wp_site, env):
    agent = say(client, "database se data Excel mein chahiye")["agents"][0]
    card = client.post(f"/api/agents/{agent['id']}/php-connector").json()["agents"][0]
    cid = card["connector"]["id"]
    assert card["connector"]["kind"] == "php" and not card["connector"]["online"]

    f = client.get(f"/api/connectors/{cid}/php-file")
    assert f.status_code == 200 and "attachment" in f.headers["content-disposition"] and "__KEY__" not in f.text
    name = re.search(r'filename="([^"]+)"', f.headers["content-disposition"]).group(1)
    (wp_site / name).write_text(f.text)

    bad = client.post(f"/api/connectors/{cid}/php-link", json={"url": f"{wp_site.base}/nothing.php"})
    assert bad.status_code == 400 and "No file" in bad.json()["error"]
    ok = client.post(f"/api/connectors/{cid}/php-link", json={"url": f"{wp_site.base}/{name}"})
    assert ok.status_code == 200, ok.text
    c = ok.json()["connector"]
    assert c["online"] and c["db_name"] == "shop_db" and "orders" in [t["name"] for t in c["tables"]]

    q = client.post(f"/api/connectors/{cid}/jobs", json={"kind": "query", "table": "orders"}).json()
    assert client.get(f"/api/connector-jobs/{q['id']}").json()["result"]["row_count"] >= 2
    e = client.post(f"/api/connectors/{cid}/jobs", json={"kind": "export", "table": "orders"}).json()
    assert "amount" in client.get(f"/api/connector-jobs/{e['id']}/download").text
    h = client.post(f"/api/connectors/{cid}/jobs", json={"kind": "health"}).json()
    assert client.get(f"/api/connector-jobs/{h['id']}").json()["status"] == "done"


def test_php_file_refuses_unsigned_replayed_and_writes(wp_site, env):
    key = "k" * 40
    text = phpconn.TEMPLATE.read_text().replace("__CONNECTOR_ID__", "1").replace("__KEY__", key).replace("__ALLOW_CHANGES__", "false")
    (wp_site / "c.php").write_text(text)
    url = f"{wp_site.base}/c.php"
    import requests
    assert requests.post(url, data='{"kind":"schema"}', timeout=10).status_code == 403          # unsigned
    assert requests.get(url, timeout=10).status_code == 404                                       # nothing on GET
    with pytest.raises(phpconn.PhpError):
        phpconn.call(url, "wrong" * 8, "schema")                                                  # wrong key
    out = phpconn.call(url, key, "query", {"sql": "DELETE FROM orders", "limit": 5})
    assert out["ok"] is False and "read-only" in out["error"]
    out = phpconn.call(url, key, "query", {"sql": "SELECT 1; DROP TABLE orders"})
    assert out["ok"] is False
    # a captured request can't be sent again
    import hashlib, hmac, json as _json, time as _t
    body = _json.dumps({"sealed": phpconn.seal(key, {"kind": "schema", "payload": {}})})
    ts, nonce = str(int(_t.time())), __import__("secrets").token_urlsafe(16)
    sig = hmac.new(key.encode(), f"{ts}\n{nonce}\n{body}".encode(), hashlib.sha256).hexdigest()
    h = {"X-Goal-Time": ts, "X-Goal-Nonce": nonce, "X-Goal-Signature": sig}
    first = requests.post(url, data=body, headers=h, timeout=10)
    assert first.status_code == 200 and "orders" not in first.text and "sealed" in first.text   # answer is encrypted
    assert "orders" in [t["name"] for t in phpconn.unseal(key, first.json()["sealed"])["result"]["tables"]]
    assert requests.post(url, data=body, headers=h, timeout=10).status_code == 403
    # a plain (unencrypted) request with SQL in it is refused
    plain = _json.dumps({"kind": "query", "payload": {"sql": "SELECT * FROM orders"}})
    ts2, n2 = str(int(_t.time())), __import__("secrets").token_urlsafe(16)
    sig2 = hmac.new(key.encode(), f"{ts2}\n{n2}\n{plain}".encode(), hashlib.sha256).hexdigest()
    assert requests.post(url, data=plain, headers={"X-Goal-Time": ts2, "X-Goal-Nonce": n2, "X-Goal-Signature": sig2}, timeout=10).status_code == 400
    # an old request is refused
    ts = str(int(_t.time()) - 600)
    sig = hmac.new(key.encode(), f"{ts}\n{'m' * 20}\n{body}".encode(), hashlib.sha256).hexdigest()
    assert requests.post(url, data=body, headers={"X-Goal-Time": ts, "X-Goal-Nonce": "m" * 20, "X-Goal-Signature": sig}, timeout=10).status_code == 403


def test_php_link_must_be_public_https(monkeypatch):
    monkeypatch.delenv("PHP_CONNECTOR_ALLOW_HTTP", raising=False)
    monkeypatch.delenv("DIRECT_DB_ALLOW_PRIVATE", raising=False)
    for url in ("http://example.com/x.php", "https://127.0.0.1/x.php", "https://localhost/x.php", "https://example.com/x.txt",
                "https://example.com:8443/x.php"):
        with pytest.raises(direct.DirectError):
            phpconn._check_url(url)


def test_removed_file_is_noticed_and_fixed_by_uploading_again(client, ml_mode, wp_site, env):
    from app.db import get_db
    from app.models import Connector

    agent = say(client, "database ki tables dikhao")["agents"][0]
    cid = client.post(f"/api/agents/{agent['id']}/php-connector").json()["agents"][0]["connector"]["id"]
    f = client.get(f"/api/connectors/{cid}/php-file")
    name = re.search(r'filename="([^"]+)"', f.headers["content-disposition"]).group(1)
    (wp_site / name).write_text(f.text)
    link = f"{wp_site.base}/{name}"
    assert client.post(f"/api/connectors/{cid}/php-link", json={"url": link}).status_code == 200

    (wp_site / name).unlink()                      # a security scanner removed it
    job = client.post(f"/api/connectors/{cid}/jobs", json={"kind": "health"}).json()
    assert client.get(f"/api/connector-jobs/{job['id']}").json()["status"] == "failed"
    c = client.get("/api/connectors").json()["connectors"][0]
    assert c["status"] == "missing" and not c["online"] and c["file_url"] == link and c["file_name"] == name

    (wp_site / name).write_text(f.text)            # uploaded again
    s = next(get_db())
    assert phpconn.check_all(s) == 1
    assert s.get(Connector, cid).status == "online"


import re  # noqa: E402


def test_php_file_hides_password_columns(wp_site, env):
    key = "p" * 40
    (wp_site / "m.php").write_text(phpconn.TEMPLATE.read_text().replace("__CONNECTOR_ID__", "2").replace("__KEY__", key).replace("__ALLOW_CHANGES__", "false"))
    url = f"{wp_site.base}/m.php"
    import subprocess as sp
    sp.run(["mysql", "-e", "USE shop_db; CREATE TABLE IF NOT EXISTS wp_users (ID INT PRIMARY KEY, user_login VARCHAR(50), "
            "user_pass VARCHAR(255), user_email VARCHAR(100)); REPLACE INTO wp_users (ID, user_login, user_pass, user_email) VALUES (1,'admin','$P$Bsecrethash','a@b.com');"])
    q = phpconn.call(url, key, "query", {"sql": "SELECT * FROM wp_users", "limit": 5})
    assert q["ok"] and "secrethash" not in str(q) and q["result"]["rows"][0][1] == "admin"
    import gzip
    path = wp_site / "out.csv.gz"
    e = phpconn.call(url, key, "export", {"sql": "SELECT * FROM wp_users"}, stream_to=path)
    text = gzip.decompress(path.read_bytes()).decode("utf-8-sig")
    assert e["ok"] and e["result"]["row_count"] == 1 and "secrethash" not in text and "admin" in text


def test_php_big_export_streams_in_pieces(wp_site, env):
    import gzip
    import subprocess as sp
    sp.run(["mysql", "-e", "USE shop_db; DROP TABLE IF EXISTS big; CREATE TABLE big (id INT PRIMARY KEY, note VARCHAR(200));"
            "INSERT INTO big SELECT seq, REPEAT('x', 150) FROM seq_1_to_20000;"], check=True)
    key = "b" * 40
    (wp_site / "b.php").write_text(phpconn.TEMPLATE.read_text().replace("__CONNECTOR_ID__", "3").replace("__KEY__", key).replace("__ALLOW_CHANGES__", "false"))
    path = wp_site / "big.csv.gz"
    out = phpconn.call(f"{wp_site.base}/b.php", key, "export", {"sql": "SELECT * FROM big"}, stream_to=path)
    assert out["result"]["row_count"] == 20000
    text = gzip.decompress(path.read_bytes()).decode("utf-8-sig")
    assert text.count("\n") == 20001 and path.stat().st_size < len(text) / 10      # several pieces, well compressed


def test_php_live_view(wp_site, env):
    key = "l" * 40
    (wp_site / "v.php").write_text(phpconn.TEMPLATE.read_text().replace("__CONNECTOR_ID__", "4").replace("__KEY__", key).replace("__ALLOW_CHANGES__", "false"))
    out = phpconn.call(f"{wp_site.base}/v.php", key, "live")
    r = out["result"]
    assert out["ok"] and r["status"]["available"] and "Questions" in r["status"]["values"]
    assert r["running"]["available"] and set(r) >= {"top", "slow", "errors"}
    for sec in ("top", "slow", "errors"):
        assert r[sec]["available"] or r[sec]["reason"]


def _items_reset():
    import subprocess as sp
    sp.run(["mysql", "-e", "USE shop_db; DELETE FROM items; INSERT INTO items (name, price, note, created, blob_col) VALUES "
            "('pen', 10.50, 'a where b', '2026-01-02 03:04:05', 0x0102), ('book', 200, NULL, '2026-02-02 10:00:00', NULL), ('bag', 900, 'x', NULL, NULL);"],
           check=True)


def _items():
    import subprocess as sp
    return sp.run(["mysql", "-N", "-e", "USE shop_db; SELECT name, price, IFNULL(note,'NULL'), IFNULL(created,'NULL'), HEX(blob_col) FROM items ORDER BY name"],
                  capture_output=True, text=True, check=True).stdout


def test_php_changes_with_undo(wp_site, env):
    _items_reset()
    key = "c" * 40
    text = phpconn.TEMPLATE.read_text().replace("__CONNECTOR_ID__", "5").replace("__KEY__", key)
    (wp_site / "on.php").write_text(text.replace("__ALLOW_CHANGES__", "true"))
    (wp_site / "off.php").write_text(text.replace("__ALLOW_CHANGES__", "false"))
    on, off = f"{wp_site.base}/on.php", f"{wp_site.base}/off.php"
    assert phpconn.call(off, key, "plan", {"sql": "DELETE FROM items WHERE id = 1"})["ok"] is False
    assert phpconn.call(on, key, "hello")["result"]["allow_changes"] is True
    start = _items()

    plan = phpconn.call(on, key, "plan", {"sql": "UPDATE items SET price = price + 1, blob_col = NULL WHERE price < 500"})
    assert plan["ok"] and plan["result"]["count"] == 2 and _items() == start
    ch = phpconn.call(on, key, "change", {"sql": "UPDATE items SET price = price + 1, blob_col = NULL WHERE price < 500", "expected": 2}, job_id=101)
    assert ch["ok"], ch
    assert _items() != start
    assert phpconn.call(on, key, "undo", {"change_job_id": 101}, job_id=102)["ok"]
    assert _items() == start                           # binary column and dates restored exactly

    ch = phpconn.call(on, key, "change", {"sql": "DELETE FROM items WHERE name = 'bag'", "expected": 1}, job_id=103)
    assert ch["ok"] and "bag" not in _items()
    assert phpconn.call(on, key, "undo", {"change_job_id": 103}, job_id=104)["ok"] and _items() == start

    ch = phpconn.call(on, key, "change", {"sql": "INSERT INTO items (name, price) VALUES ('cup', 3), ('mug', 4)"}, job_id=105)
    assert ch["ok"] and ch["result"]["rows"] == 2
    assert phpconn.call(on, key, "undo", {"change_job_id": 105}, job_id=106)["ok"] and _items() == start

    # stale preview count: nothing happens
    bad = phpconn.call(on, key, "change", {"sql": "DELETE FROM items WHERE price > 100", "expected": 7}, job_id=107)
    assert bad["ok"] is False and "changed since the preview" in bad["error"] and _items() == start

    # someone edits after our change: undo refuses, then works when forced
    phpconn.call(on, key, "change", {"sql": "UPDATE items SET note = 'v2' WHERE name = 'book'", "expected": 1}, job_id=108)
    import subprocess as sp
    sp.run(["mysql", "-e", "USE shop_db; UPDATE items SET note = 'by the app' WHERE name = 'book'"], check=True)
    c = phpconn.call(on, key, "undo", {"change_job_id": 108}, job_id=109)
    assert c["ok"] is False and c["error"].startswith("CONFLICT") and "by the app" in _items()
    assert phpconn.call(on, key, "undo", {"change_job_id": 108, "force": True}, job_id=110)["ok"] and _items() == start

    # the undo copy is encrypted on the hosting, outside the web folder
    copies = list((wp_site.path.parent).glob(".goal-undo-*/*.undo"))
    assert all("book" not in p.read_text() for p in copies)


@pytest.mark.skipif(not _up(5432), reason="needs local PostgreSQL")
def test_php_changes_with_undo_on_postgres(wp_site, env):
    import subprocess as sp
    psql = ["psql", "-h", "127.0.0.1", "-U", "shopapp", "shop_pg", "-At", "-c"]
    envp = {**__import__("os").environ, "PGPASSWORD": "app pass"}
    sp.run(psql + ["DELETE FROM items; INSERT INTO items (name, price, note, created, blob_col) VALUES ('pen', 10.50, 'a where b', "
                   "'2026-01-02 03:04:05', '\\x0102'), ('book', 200, NULL, '2026-02-02 10:00:00', NULL), ('bag', 900, 'x', NULL, NULL);"],
           env=envp, check=True)
    snap = lambda: sp.run(psql + ["SELECT name, price, note, created, encode(blob_col, 'hex') FROM items ORDER BY name"],  # noqa: E731
                          env=envp, capture_output=True, text=True, check=True).stdout
    key = "g" * 40
    text = (phpconn.TEMPLATE.read_text().replace("__CONNECTOR_ID__", "6").replace("__KEY__", key).replace("__ALLOW_CHANGES__", "true")
            .replace("$GOAL_DB = ['type' => '', 'host' => '', 'port' => '', 'database' => '', 'user' => '', 'password' => ''];",
                     "$GOAL_DB = ['type' => 'pgsql', 'host' => '127.0.0.1', 'port' => '5432', 'database' => 'shop_pg', 'user' => 'shopapp', 'password' => 'app pass'];"))
    (wp_site / "pg.php").write_text(text)
    url = f"{wp_site.base}/pg.php"
    start = snap()
    for n, sql in enumerate(("UPDATE items SET price = 1, blob_col = NULL WHERE name IN ('pen', 'book')",
                             "DELETE FROM items WHERE name = 'pen'", "INSERT INTO items (name, price) VALUES ('cup', 3)")):
        plan = phpconn.call(url, key, "plan", {"sql": sql})
        assert plan["ok"], plan
        ch = phpconn.call(url, key, "change", {"sql": sql, "expected": plan["result"]["count"]}, job_id=200 + n)
        assert ch["ok"], ch
        assert snap() != start
        u = phpconn.call(url, key, "undo", {"change_job_id": 200 + n}, job_id=300 + n)
        assert u["ok"], u
        assert snap() == start, sql


def test_php_blocks_cascade_deletes(wp_site, env):
    key = "d" * 40
    (wp_site / "cas.php").write_text(phpconn.TEMPLATE.read_text().replace("__CONNECTOR_ID__", "7").replace("__KEY__", key)
                                     .replace("__ALLOW_CHANGES__", "true"))
    url = f"{wp_site.base}/cas.php"
    plan = phpconn.call(url, key, "plan", {"sql": "DELETE FROM parents WHERE id = 1"})
    assert plan["ok"] and "order_lines" in plan["result"]["blocked"]
    bad = phpconn.call(url, key, "change", {"sql": "DELETE FROM parents WHERE id = 1", "expected": 1}, job_id=900)
    assert bad["ok"] is False and "cascade" in bad["error"]


def test_php_checks_optimize_and_index_fix(wp_site, env):
    key = "o" * 40
    (wp_site / "opt.php").write_text(phpconn.TEMPLATE.read_text().replace("__CONNECTOR_ID__", "8").replace("__KEY__", key)
                                     .replace("__ALLOW_CHANGES__", "true"))
    url = f"{wp_site.base}/opt.php"
    c = phpconn.call(url, key, "checks", {"queries": ["SHOW VARIABLES LIKE 'max_connections'", "SELECT * FROM no_such_table"]})
    assert c["ok"] and c["result"]["results"][0]["rows"] and "error" in c["result"]["results"][1]
    o = phpconn.call(url, key, "optimize", {"sql": "SELECT id, note FROM big WHERE id + 0 > 19000 ORDER BY id",
                                            "rewrites": ["SELECT id, note FROM big WHERE id > 19000 ORDER BY id"], "index_tables": ["big"]})
    r = o["result"]
    assert r["candidates"][0]["same"] is True and r["candidates"][0]["plan"] < r["original"]["plan"] and r["tables"]["big"]["rows"] == 20000
    import subprocess as sp
    sp.run(["mysql", "-e", "USE shop_db; ALTER TABLE items DROP INDEX IF EXISTS idx_php_price"], check=True)
    plan = phpconn.call(url, key, "plan", {"sql": "ALTER TABLE items ADD INDEX idx_php_price (price)"})
    assert plan["ok"] and not plan["result"].get("blocked"), plan
    ch = phpconn.call(url, key, "change", {"sql": "ALTER TABLE items ADD INDEX idx_php_price (price)"}, job_id=950)
    assert ch["ok"], ch
    assert "idx_php_price" in sp.run(["mysql", "-N", "-e", "SHOW INDEX FROM shop_db.items"], capture_output=True, text=True).stdout
    assert phpconn.call(url, key, "undo", {"change_job_id": 950}, job_id=951)["ok"]
    assert "idx_php_price" not in sp.run(["mysql", "-N", "-e", "SHOW INDEX FROM shop_db.items"], capture_output=True, text=True).stdout
