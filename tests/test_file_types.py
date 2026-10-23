"""Old Excel (.xls), OpenDocument (.ods), renamed and password-protected files: accepted and read, or explained plainly."""

import pytest

from tests.test_ml import say
from tests.test_office_agent import _upload


def _xls(path):
    xlwt = pytest.importorskip("xlwt")
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Fees")
    for c, h in enumerate(["Roll No", "Name", "Class", "Fee"]):
        ws.write(0, c, h)
    rows = [(1, "Aditi Sharma", "6", 1500), (2, "Rohan Gill", "7", 2000), (3, "Neha Khan", "6", 1500), (4, "Arjun Rana", "7", 2500)]
    for r, row in enumerate(rows, start=1):
        for c, v in enumerate(row):
            ws.write(r, c, v)
    wb.save(str(path))


def _ods(path):
    pytest.importorskip("odf")
    from odf.opendocument import OpenDocumentSpreadsheet
    from odf.table import Table, TableCell, TableRow
    from odf.text import P

    doc = OpenDocumentSpreadsheet()
    t = Table(name="Stock")
    for row in [["Item", "Qty"], ["Soap", 10], ["Tea", 25], ["Rice", 5]]:
        tr = TableRow()
        for v in row:
            cell = TableCell(valuetype="float", value=v) if isinstance(v, (int, float)) else TableCell(valuetype="string")
            cell.addElement(P(text=str(v)))
            tr.addElement(cell)
        t.addElement(tr)
    doc.spreadsheet.addElement(t)
    doc.save(str(path))


def _open(client, name, data):
    r = say(client, "I want to work on my excel file")
    agent = next(a for a in r["agents"] if a.get("office"))
    f = _upload(client, agent["id"], data, name)
    return r["goal"]["id"], f


def test_old_xls_is_converted_and_answered(client, ml_mode, tmp_path):
    _xls(tmp_path / "fees.xls")
    gid, f = _open(client, "fees.xls", (tmp_path / "fees.xls").read_bytes())
    assert f["status"] == "ready", f.get("error")
    r = say(client, "total fee", gid)
    assert "7,500" in r["reply"], r["reply"]


def test_xls_renamed_to_xlsx_still_works(client, ml_mode, tmp_path):
    _xls(tmp_path / "fees.xls")
    gid, f = _open(client, "fees.xlsx", (tmp_path / "fees.xls").read_bytes())
    assert f["status"] == "ready", f.get("error")
    assert "4 records" in say(client, "how many students", gid)["reply"]


def test_ods_is_converted_and_answered(client, ml_mode, tmp_path):
    _ods(tmp_path / "stock.ods")
    gid, f = _open(client, "stock.ods", (tmp_path / "stock.ods").read_bytes())
    assert f["status"] == "ready", f.get("error")
    assert "40" in say(client, "total qty", gid)["reply"]


def test_password_protected_file_is_explained(client, ml_mode):
    fake = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512 + "EncryptionInfo".encode("utf-16-le") + b"\x00" * 2048
    _, f = _open(client, "locked.xlsx", fake)
    assert f["status"] == "error" and "password" in f["error"].lower()


def test_not_an_excel_file_is_explained(client, ml_mode):
    _, f = _open(client, "photo.xlsx", b"\x89PNG\r\n" + b"\x00" * 300)
    assert f["status"] == "error" and "isn't a real Excel file" in f["error"]


def test_google_sheet_link_is_fetched_and_answered(client, ml_mode, tmp_path, monkeypatch):
    from openpyxl import Workbook

    from app.agents import office_agent
    from tests.test_office_agent import _wait

    wb = Workbook()
    ws = wb.active
    ws.title = "Orders"
    ws.append(["Order", "Region", "Amount"])
    for i, (reg, amt) in enumerate([("North", 100), ("South", 250), ("North", 50)], start=1):
        ws.append([f"O{i}", reg, amt])
    wb.save(tmp_path / "g.xlsx")
    monkeypatch.setattr(office_agent, "fetch_sheet", lambda sid: (tmp_path / "g.xlsx").read_bytes())
    r = say(client, "please check this sheet https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit#gid=0")
    assert "Got your Google Sheet" in r["reply"], r["reply"]
    agent = next(a for a in r["agents"] if a.get("office"))
    f = _wait(client, agent["files"][0]["id"])
    assert f["status"] == "ready"
    assert "400" in say(client, "total amount", r["goal"]["id"])["reply"]


def test_private_google_sheet_says_how_to_share(client, ml_mode, monkeypatch):
    from app.agents import office_agent

    def private(sid):
        raise office_agent.OfficeAgentError("I can't open this Google Sheet: it isn't shared. In the sheet, click Share, set General access "
                                            "to \"Anyone with the link\" (Viewer), and send the link again.")
    monkeypatch.setattr(office_agent, "fetch_sheet", private)
    r = say(client, "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit")
    assert "Anyone with the link" in r["reply"]
