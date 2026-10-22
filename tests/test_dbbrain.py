"""The database brain recognises problems from how people actually say them (no AI)."""

from app.dbknow import brain


def test_recognises_issues_errors_sql_and_tasks():
    cases = {
        "mere site pe too many connections error aa raha hai": ("issue", "too-many-connections"),
        "database mein hindi text ???? ban jata hai": ("issue", "question-marks-instead-of-text"),
        "query bahut slow hai kya karun": ("do", "slow_query"),
        "previous developer ne kuch nahi bataya, database samjhao": ("do", "understand"),
        "pichle 6 mahine mein har mahine kitne orders aaye": ("question", "count-by-month"),
    }
    for text, (kind, ident) in cases.items():
        r = brain.recognize(text)
        assert (r["kind"], r["id"]) == (kind, ident), (text, r)
    assert brain.recognize("SELECT * FROM orders WHERE YEAR(created_at) = 2026")["kind"] == "sql"
    assert brain.recognize("ERROR 1054 (42S22): Unknown column 'ordr' in 'field list'")["id"] == "mysql-unknown-column"
    assert brain.recognize('ERROR: column "Name" does not exist', engine="postgres")["id"] == "pg-undefined-column"


def test_chat_routes_db_problems_and_leaves_others_alone(client, ml_mode):
    from tests.test_ml import say
    r = say(client, "site pe baar baar too many connections error aa raha hai")
    a = r["agents"][0]
    assert a["recipe"] == "database" and a["brain"]["kind"] == "issue" and a["brain"]["id"] == "too-many-connections"
    assert "connections" in r["reply"].lower()
    client.post("/api/new-chat"); client.cookies.delete("ge_vid")
    other = say(client, "mera business slow chal raha hai, customers kam hain")
    assert all(x["recipe"] != "database" for x in other["agents"])


def test_diagnose_and_optimize_through_the_api(client, ml_mode, monkeypatch):
    import socket
    try:
        socket.create_connection(("127.0.0.1", 3306), timeout=1).close()
    except OSError:
        import pytest
        pytest.skip("needs local MySQL")
    from app.agents import direct
    monkeypatch.setenv("APP_SECRET", "s" * 32)
    monkeypatch.setenv("DIRECT_DB_ALLOW_PRIVATE", "1")
    monkeypatch.setenv("DIRECT_DB_SYNC", "1")
    direct._attempts.clear()
    from tests.test_ml import say
    agent = say(client, "database pe too many connections aa raha hai")["agents"][0]
    res = client.post("/api/connectors/direct", json={"host": "127.0.0.1", "database": "shop_db", "user": "shopapp",
                                                      "password": "app pass", "agent_id": agent["id"]})
    cid = res.json()["agents"][0]["connector"]["id"]
    d = client.post(f"/api/connectors/{cid}/diagnose", json={"issue_id": "too-many-connections"}).json()
    job = client.get(f"/api/connector-jobs/{d['job']['id']}").json()
    assert job["status"] == "done" and len(job["result"]["results"]) == len(d["checks"]) and d["playbook"]["causes"]
    o = client.post(f"/api/connectors/{cid}/optimize", json={"sql": "SELECT id, note FROM big WHERE YEAR(NOW()) = 2026 AND id + 0 > 19000"}).json()
    assert "analysis" in o and o["analysis"]["findings"]
    oj = client.get(f"/api/connector-jobs/{o['job']['id']}").json()
    assert oj["status"] == "done" and oj["result"]["original"]["count"] == 1000


def test_brain_understands_new_phrasings_of_every_problem():
    """Each playbook/question ships 2 sentences the brain never learned from. It must still recognise them."""
    from app import dbknow
    misses = []
    for name, kind in (("issues", "issue"), ("questions", "question")):
        for e in dbknow.load(name):
            for t in e.get("test_phrases", []):
                r = brain.recognize(t)
                if (r["kind"], r["id"]) != (kind, e["id"]):
                    misses.append(f"{e['id']}: '{t}' -> {r['kind']}:{r['id']} ({r['confidence']})")
    assert not misses, "\n".join(misses)


def test_brain_understands_hindi_script_and_voice_style():
    from app.dbknow import brain

    cases = {
        "मेरा डेटाबेस बहुत धीरे चल रहा है": "do:slow_query",
        "डेटाबेस में बहुत सारे कनेक्शन की एरर आ रही है": "issue:too-many-connections",
        "डुप्लीकेट ईमेल ढूंढो": "question:duplicates",
        "database ka structure samjhao mujhe": "do:understand",
    }
    for text, want in cases.items():
        r = brain.recognize(text)
        assert f"{r['kind']}:{r['id']}" == want, (text, r)
