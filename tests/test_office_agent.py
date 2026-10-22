"""Microsoft agent end to end: chat -> card -> upload in pieces -> automatic check -> fix -> download -> compare -> cleanup."""

import io
import time

from openpyxl import Workbook, load_workbook

from tests.test_ml import say


def _xlsx(rows, broken=False):
    wb = Workbook()
    ws = wb.active
    ws.title = "Sales"
    ws.append(["Invoice No", "Customer", "Qty", "Rate", "Amount"])
    for i, (inv, cust, q, r) in enumerate(rows, start=2):
        ws.append([inv, cust, q, r, f"=C{i}*D{i}"])
    if broken:
        ws["E4"] = "=C4*#REF!"
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def _upload(client, agent_id, data, name="sales.xlsx"):
    f = client.post("/api/office/files", json={"name": name, "size": len(data), "agent_id": agent_id}).json()
    half = len(data) // 2
    client.put(f"/api/office/files/{f['id']}/chunk?offset=0", content=data[:half])
    client.put(f"/api/office/files/{f['id']}/chunk?offset=0", content=data[:half])   # a retried piece is ignored
    r = client.put(f"/api/office/files/{f['id']}/chunk?offset={half}", content=data[half:]).json()
    assert r["received"] == len(data)
    client.post(f"/api/office/files/{f['id']}/done")
    return _wait(client, f["id"])


def _wait(client, token):
    for _ in range(200):
        f = client.get(f"/api/office/files/{token}").json()
        if f["status"] in ("ready", "error"):
            return f
        time.sleep(0.05)
    raise AssertionError("still working")


ROWS = [(f"INV{i}", f" Cust  {i} ", i, "1,200" if i == 3 else 100) for i in range(1, 9)]


def test_chat_to_fixed_file(client, ml_mode):
    r = say(client, "meri excel sheet mein #REF! aa raha hai row delete karne ke baad")
    agent = next(a for a in r["agents"] if a.get("office"))
    assert agent["brain"]["id"] == "excel-ref-error"
    assert "Samjha" in r["reply"] or "Understood" in r["reply"]

    f = _upload(client, agent["id"], _xlsx(ROWS, broken=True))
    assert f["status"] == "ready", f
    s = f["report"]["summary"]
    assert s["broken_refs"] == 1 and s["text_numbers"] == 1 and s["spaces"] >= 8

    client.post(f"/api/office/files/{f['id']}/fix", json={"ops": ["trim_spaces", "text_to_number", "fix_broken_refs"]})
    f = _wait(client, f["id"])
    assert f["download"] and f["result"]["changes"]["fix_broken_refs"] == 1
    d = client.get(f"/api/office/files/{f['id']}/download")
    ws = load_workbook(io.BytesIO(d.content))["Sales"]
    assert ws["E4"].value == "=C4*D4" and ws["D4"].value == 1200 and ws["B2"].value == "Cust 1"


def test_compare_two_uploads(client, ml_mode):
    r = say(client, "do excel sheet compare karni hai, data match nahi ho raha")
    agent = next(a for a in r["agents"] if a.get("office"))
    a = _upload(client, agent["id"], _xlsx(ROWS), "old.xlsx")
    new_rows = [row if row[0] != "INV2" else ("INV2", " Cust  2 ", 2, 999) for row in ROWS[1:]] + [("INV99", "New", 1, 1)]
    b = _upload(client, agent["id"], _xlsx(new_rows), "new.xlsx")
    client.post(f"/api/office/files/{a['id']}/compare", json={"other": b["id"]})
    a = _wait(client, a["id"])
    res = a["result"]
    assert res["key"] == "Invoice No" and (res["only_in_a"], res["only_in_b"], res["changed_rows"]) == (1, 1, 1)
    assert a["download"]


def test_files_are_private_and_cleaned(client, ml_mode):
    r = say(client, "excel file ka data saaf karna hai")
    agent = next(a for a in r["agents"] if a.get("office"))
    f = _upload(client, agent["id"], _xlsx(ROWS))
    other = client.__class__(client.app)
    other.cookies.set("ge_vid", "someone-else-000000000000000000")
    assert other.get(f"/api/office/files/{f['id']}").status_code == 400
    assert client.post("/api/office/files", json={"name": "virus.exe", "size": 10}).status_code == 400
    client.post("/api/privacy/delete-all")
    from app.db import _SessionLocal
    from app.models import OfficeFile

    with _SessionLocal() as db:
        assert db.query(OfficeFile).filter(OfficeFile.token == f["id"]).count() == 0


def test_old_files_removed_after_an_hour(client, ml_mode):
    from datetime import timedelta

    from app.agents import office_agent
    from app.db import _SessionLocal
    from app.models import OfficeFile, utcnow

    r = say(client, "csv file mein duplicate hatane hain")
    agent = next(a for a in r["agents"] if a.get("office"))
    f = _upload(client, agent["id"], b"Name,Phone\nA,1\nA,1\n", "list.csv")
    with _SessionLocal() as db:
        row = db.query(OfficeFile).filter(OfficeFile.token == f["id"]).one()
        path = row.path
        assert office_agent.maintain(db, utcnow() + timedelta(hours=2)) >= 1
    import os

    assert not os.path.exists(path)
