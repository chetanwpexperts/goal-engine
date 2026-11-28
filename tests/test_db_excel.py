"""S3 through the real API: an export job becomes a workbook (Data, Pivot with live formulas and a chart, About), the chat
card gets the chart, the person's words pick the pivot, the scheduled report email carries the workbook, and nobody else
can fetch it. The concept questions about it reach their answers, and real work still goes where it went."""

import io
import re
import sqlite3

import pytest
from openpyxl import load_workbook

from app import main
from tests.test_database import Mail, gc, pump
from tests.test_ml import say


@pytest.fixture()
def parcel_db(tmp_path):
    path = tmp_path / "parcels.sqlite"
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE parcels (id INTEGER PRIMARY KEY, hub TEXT, status TEXT, weight_kg INTEGER, fee REAL, code TEXT);")
    rows = [(f"Hub {i % 4}", ["booked", "delivered", "returned"][i % 3], i % 9 + 1, round(i * 3.25, 2), f"{i:05d}") for i in range(60)]
    con.executemany("INSERT INTO parcels (hub, status, weight_kg, fee, code) VALUES (?, ?, ?, ?, ?)", rows)
    con.commit()
    con.close()
    return str(path), rows


def _connect(client, db_path, text="muje mera data excel sheet me dalna hai database se, status wise fee ka total"):
    res = say(client, text)
    assert res["agents"], (res["relation"], res["reply"][:300])
    agent = res["agents"][0]
    token = client.post("/api/connectors", json={"agent_id": agent["id"]}).json()["token"]
    db = gc.Database({"type": "sqlite", "database": db_path}).connect()
    client.post("/api/connector/hello", headers={"Authorization": f"Bearer {token}"}, json={"db_type": "sqlite", "version": "3.45"})
    pump(client, token, db)
    cid = client.get("/api/connectors").json()["connectors"][0]["id"]
    return res, agent, token, db, cid


def test_export_becomes_a_workbook(client, ml_mode, parcel_db):
    path, rows = parcel_db
    res, agent, token, db, cid = _connect(client, path)
    assert agent.get("database") and "status wise" in agent["excel_words"]
    job = client.post(f"/api/connectors/{cid}/jobs", json={"kind": "export", "table": "parcels"}).json()
    pump(client, token, db)
    x = client.post(f"/api/connector-jobs/{job['id']}/excel", json={"words": agent["excel_words"]}).json()
    assert x["pivot"]["by"] == "status" and x["pivot"]["measure"] == "fee" and x["rows"] == 60
    assert x["sheets"] == ["Data", "Pivot", "About"] and x["chart"]["rows"] and "Excel file ready" in x["say"]
    totals = {}
    for hub, status, w, fee, code in rows:
        totals[status] = totals.get(status, 0) + fee
    assert {r[0]: round(r[1], 2) for r in x["chart"]["rows"]} == {k: round(v, 2) for k, v in totals.items()}
    f = client.get(x["download"])
    assert f.status_code == 200 and f.headers["content-type"].startswith("application/vnd.openxmlformats")
    wb = load_workbook(io.BytesIO(f.content))
    assert wb.sheetnames == ["Data", "Pivot", "About"]
    assert wb["Data"]["F2"].value == "00000" and wb["Data"]["F2"].data_type == "s"          # codes keep their zeros
    assert wb["Pivot"]["C2"].value.startswith("=SUMIFS(Data!$E$2:$E$61,Data!$C$2:$C$61,$A2")  # live, over the Data sheet
    import zipfile

    assert any(n.startswith("xl/charts/chart") for n in zipfile.ZipFile(io.BytesIO(f.content)).namelist())   # the chart is in the file
    # another column, the person's own pick
    y = client.post(f"/api/connector-jobs/{job['id']}/excel", json={"by": "hub", "measure": "weight_kg"}).json()
    assert y["pivot"]["by"] == "hub" and y["pivot"]["measure"] == "weight_kg" and y["download"] != x["download"]
    # nobody else gets it
    client.cookies.clear()
    assert client.post(f"/api/connector-jobs/{job['id']}/excel", json={}).status_code == 404
    assert client.get(x["download"]).status_code == 404


def test_bad_tag_and_unfinished_job(client, ml_mode, parcel_db):
    path, _ = parcel_db
    res, agent, token, db, cid = _connect(client, path)
    job = client.post(f"/api/connectors/{cid}/jobs", json={"kind": "export", "table": "parcels"}).json()
    assert client.post(f"/api/connector-jobs/{job['id']}/excel", json={}).status_code == 400      # not exported yet
    pump(client, token, db)
    assert client.get(f"/api/connector-jobs/{job['id']}/excel?tag=../../etc").status_code == 404
    q = client.post(f"/api/connectors/{cid}/jobs", json={"kind": "query", "table": "parcels"}).json()
    pump(client, token, db)
    assert client.post(f"/api/connector-jobs/{q['id']}/excel", json={}).status_code == 400        # only exports


def test_hinglish_reply(client, ml_mode, parcel_db):
    path, _ = parcel_db
    res, agent, token, db, cid = _connect(client, path)
    job = client.post(f"/api/connectors/{cid}/jobs", json={"kind": "export", "sql": "SELECT status, fee FROM parcels"}).json()
    pump(client, token, db)
    x = client.post(f"/api/connector-jobs/{job['id']}/excel", json={"hi": True}).json()
    assert x["say"].startswith("Excel file taiyar")


def test_scheduled_report_attaches_the_workbook(client, ml_mode, parcel_db, monkeypatch):
    path, _ = parcel_db
    mail = Mail()
    monkeypatch.setattr(main, "mailer", lambda: mail)
    agent = say(client, "roz subah database se parcels ki report email karo")["agents"][0]
    token = client.post("/api/connectors", json={"agent_id": agent["id"]}).json()["token"]
    db = gc.Database({"type": "sqlite", "database": path}).connect()
    client.post("/api/connector/hello", headers={"Authorization": f"Bearer {token}"}, json={"db_type": "sqlite"})
    pump(client, token, db)
    client.post("/api/owner/code", json={"email": "owner@parcel.test", "business": "Parcels"})
    client.post("/api/owner/verify", json={"code": re.search(r"\d{6}", mail.sent[-1]["body"]).group(0)})
    assert client.post(f"/api/agents/{agent['id']}/db-report", json={"table": "parcels", "every": "day", "at": "09:00"}).status_code == 200
    from app.core.db import get_db
    from app.domain.models import Agent

    s = next(main.app.dependency_overrides.get(get_db, get_db)())
    main._run_db_report(s, s.query(Agent).filter_by(recipe="db_report").one())
    pump(client, token, db)
    names = [a[0] for a in mail.sent[-1]["attachments"]]
    assert names[0].endswith(".csv") and names[1].endswith(".xlsx") and "Pivot sheet" in mail.sent[-1]["body"]
    assert load_workbook(io.BytesIO(mail.sent[-1]["attachments"][1][1])).sheetnames == ["Data", "Pivot", "About"]


def test_old_exports_are_cleaned(tmp_path, monkeypatch):
    """Exports are gzip CSVs (job_N_x.csv.gz) and their workbooks: both go after a day (the old *.csv pattern missed them)."""
    import os
    import time

    from app.agents import database as dbagent

    monkeypatch.setattr(dbagent, "EXPORT_DIR", tmp_path)
    old = [tmp_path / "job_1_ab.csv.gz", tmp_path / "job_1_ab.csv.gz.0123456789.xlsx"]
    new = tmp_path / "job_2_cd.csv.gz"
    for f in old + [new]:
        f.write_bytes(b"x")
    for f in old:
        os.utime(f, (time.time() - 3 * 86400, time.time() - 3 * 86400))
    dbagent.clean_exports()
    assert not any(f.exists() for f in old) and new.exists()


WRONG = re.compile(r"I don't have a specific answer|paste (the|your|a) (sql )?query|query paste karo|connect your database|"
                   r"Bas batao database kahan hai|Just tell me where your database is", re.I)


@pytest.mark.parametrize("cid", ["query_to_excel", "group_by_capitals"])
def test_concept_phrasings(client, ml_mode, cid):
    from app.products.dbaba.dbknow import concepts as K

    c = K.by_id(cid)
    titles = set(c["title"].values())
    for lang, phr in c["phrasings"].items():
        for text in phr:
            client.post("/api/new-chat")
            client.cookies.clear()
            r = say(client, text)
            assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in titles and not WRONG.search(r["reply"]), \
                (lang, text, r["relation"], r["reply"][:200])


@pytest.mark.parametrize("text", [
    "SELECT status, COUNT(*) FROM parcels GROUP BY status",                      # a pasted query: the checker
    "ERROR 1054 (42S22): Unknown column 'stauts' in 'field list'",               # an error: the diagnoser
    "my database is slow",                                                       # a complaint: the plan
])
def test_real_work_still_goes_where_it_went(client, ml_mode, text):
    r = say(client, text)
    assert r["relation"] != "concept", (text, r["reply"][:200])
