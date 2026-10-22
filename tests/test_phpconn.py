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
    text = phpconn.TEMPLATE.read_text().replace("__CONNECTOR_ID__", "1").replace("__KEY__", key)
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
    (wp_site / "m.php").write_text(phpconn.TEMPLATE.read_text().replace("__CONNECTOR_ID__", "2").replace("__KEY__", key))
    url = f"{wp_site.base}/m.php"
    import subprocess as sp
    sp.run(["mysql", "-e", "USE shop_db; CREATE TABLE IF NOT EXISTS wp_users (ID INT PRIMARY KEY, user_login VARCHAR(50), "
            "user_pass VARCHAR(255), user_email VARCHAR(100)); REPLACE INTO wp_users VALUES (1,'admin','$P$Bsecrethash','a@b.com');"])
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
    (wp_site / "b.php").write_text(phpconn.TEMPLATE.read_text().replace("__CONNECTOR_ID__", "3").replace("__KEY__", key))
    path = wp_site / "big.csv.gz"
    out = phpconn.call(f"{wp_site.base}/b.php", key, "export", {"sql": "SELECT * FROM big"}, stream_to=path)
    assert out["result"]["row_count"] == 20000
    text = gzip.decompress(path.read_bytes()).decode("utf-8-sig")
    assert text.count("\n") == 20001 and path.stat().st_size < len(text) / 10      # several pieces, well compressed


def test_php_live_view(wp_site, env):
    key = "l" * 40
    (wp_site / "v.php").write_text(phpconn.TEMPLATE.read_text().replace("__CONNECTOR_ID__", "4").replace("__KEY__", key))
    out = phpconn.call(f"{wp_site.base}/v.php", key, "live")
    r = out["result"]
    assert out["ok"] and r["status"]["available"] and "Questions" in r["status"]["values"]
    assert r["running"]["available"] and set(r) >= {"top", "slow", "errors"}
    for sec in ("top", "slow", "errors"):
        assert r[sec]["available"] or r[sec]["reason"]
