"""Who is reviewing: the name entered on the start page (no account, no password) signs approvals and assessments.

The clients here start anonymous (tests/conftest.py lets this module opt out of the test reviewer).
"""

import sqlite3
import stat
from contextlib import closing
from http.cookies import SimpleCookie
from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest
from itsdangerous import TimestampSigner

import app as dashboard
import review_store
from test_flow_review_app import JSON, add_draft, case, client, page  # noqa: F401

FREI = {"name": "Frei Thomas", "initials": "FRT", "function": "Rechtsanwalt"}
SIGNER = {"name": "Frei Thomas", "initials": "FRT"}


def start(client, **reviewer):
    return client.post("/api/session", json=reviewer or FREI)


@pytest.mark.parametrize("route", ["/approve", "/run", "/add", "/api/triage-feedback", "/briefing"])
def test_writes_wait_for_a_reviewer_name_before_validating_anything(client, route):
    assert client.post(route, headers=JSON).status_code == 401


def test_only_the_page_shell_and_the_session_are_open_before_the_name_is_entered(client, case):
    for path in ("/api/dashboard", "/api/library", f"/api/export?case={case}", "/briefing/x.mp3?lang=de"):
        assert client.get(path).status_code == 401, path
    for path in ("/api/case-search", "/api/library/ask", "/conversation/ask"):
        assert client.post(path, json={}).status_code == 401, path
    assert client.get("/api/session").json == {"reviewer": None}
    assert client.get("/start").status_code in (200, 503)  # 503 only while the frontend is not built


def test_the_name_is_checked_tidied_and_kept_in_an_httponly_cookie(client):
    for bad in ({"name": "F", "initials": "FRT"}, {"name": "Frei Thomas", "initials": ""},
                {"name": "Frei Thomas", "initials": "F R T!"}, {"name": "Frei Thomas", "initials": "TOOLONGX"}):
        assert start(client, **bad).status_code == 400
    response = start(client, name="  Frei   Thomas ", initials="frt", function=" Rechtsanwalt ")
    assert response.json["reviewer"] == FREI
    cookies = SimpleCookie()
    for header in response.headers.getlist("Set-Cookie"):
        cookies.load(header)
    cookie = cookies[dashboard.app.config["SESSION_COOKIE_NAME"]]
    assert cookie["httponly"] and cookie["samesite"].lower() == "lax"
    assert client.get("/api/session").json == {"reviewer": FREI}
    assert client.get("/api/dashboard").status_code == 200
    assert client.post("/api/session/end").json == {"reviewer": None}
    assert client.get("/api/dashboard").status_code == 401


def test_reviewer_cookie_expires_after_twelve_hours(client, monkeypatch):
    issued_at = 1_800_000_000
    clock = issued_at
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: clock)
    monkeypatch.setitem(dashboard.app.config, "SESSION_REFRESH_EACH_REQUEST", False)

    response = start(client, name="Frei Thomas", initials="FRT")
    assert response.status_code == 200
    reviewer = FREI | {"function": ""}
    assert response.json == {"reviewer": reviewer}

    clock = issued_at + 12 * 60 * 60 - 1
    assert client.get("/api/session").json == {"reviewer": reviewer}
    clock += 2
    assert client.get("/api/session").json == {"reviewer": None}
    assert client.post("/approve", headers=JSON).status_code == 401


def test_another_site_cannot_set_or_end_the_reviewer(client):
    assert client.post("/api/session", json=FREI, headers={"Origin": "https://outside.example"}).status_code == 403
    assert start(client).status_code == 200
    assert client.post("/api/session/end", headers={"Origin": "https://outside.example"}).status_code == 403
    assert client.get("/api/session").json == {"reviewer": FREI}


def test_the_signer_comes_from_the_session_and_follows_the_approved_summary(client, case):
    assert start(client).status_code == 200
    for lang, text in (("de", "Das Bundesgericht bestätigt die Leistungspflicht."),
                       ("fr", "Le Tribunal fédéral confirme la prise en charge.")):
        response = client.post("/approve", headers=JSON, data={
            "case": case, "lang": lang, "version": add_draft(case, lang), "summary": text,
            "previous_approved": "", "signer": "Forged reviewer", "initials": "BAD"})
        assert response.status_code == 200
    response = client.post("/api/triage-feedback", json={
        "case": case, "status": "Relevant", "reason": "Präzisiert die Leistungspflicht.",
        "signer": {"name": "Forged reviewer", "initials": "BAD"}})
    assert response.json["triage_feedback"]["signer"] == SIGNER

    selected = page(client, case=case)["selected"]
    assert selected["triage_feedback"]["signer"] == SIGNER
    assert selected["de"]["signer"] == selected["fr"]["signer"] == SIGNER
    assert selected["de"]["signer_function"] == "Rechtsanwalt" and selected["de"]["signed_at"]

    hits = [hit for hit in client.get("/api/library").json["hits"] if hit["key"] == case]
    assert {hit["language"] for hit in hits} == {"de", "fr"} and all(hit["signer"] == SIGNER for hit in hits)

    text = client.get("/api/export", query_string={"case": case, "format": "html"}).get_data(as_text=True)
    assert "<h2>Autor</h2><p>Frei Thomas (FRT), Rechtsanwalt</p>" in text
    assert "Forged reviewer" not in text


def test_an_approved_edit_by_another_reviewer_is_signed_by_them_and_kept_in_the_history(client, case):
    assert start(client).status_code == 200
    version = add_draft(case, "de")
    first = "Das Bundesgericht bestätigt die Leistungspflicht."
    assert client.post("/approve", headers=JSON, data={"case": case, "lang": "de", "version": version,
                                                       "summary": first, "previous_approved": ""}).status_code == 200
    assert start(client, name="Villa Rebeca", initials="VIL").status_code == 200
    assert client.post("/approve", headers=JSON, data={"case": case, "lang": "de", "version": version,
                                                       "summary": first + " Ergänzt.",
                                                       "previous_approved": first}).status_code == 200
    assert page(client, case=case)["selected"]["de"]["signer"] == {"name": "Villa Rebeca", "initials": "VIL"}
    with closing(dashboard.connect()) as db:
        assert [row["initials"] for row in db.execute("SELECT initials FROM signatures ORDER BY id")] == ["FRT", "VIL"]


@pytest.mark.parametrize("format", ["html", "docx", "pdf"])
def test_export_ends_with_the_current_author_for_each_language(client, case, format):
    assert start(client).status_code == 200
    de_version = add_draft(case, "de")
    original = "Das Bundesgericht bestätigt die Leistungspflicht."
    de_approval = {"case": case, "lang": "de", "version": de_version}
    assert client.post("/approve", headers=JSON, data=de_approval | {
        "summary": original, "previous_approved": "",
    }).status_code == 200
    assert client.post("/approve", headers=JSON, data={
        "case": case, "lang": "fr", "version": add_draft(case, "fr"),
        "summary": "Le Tribunal fédéral confirme la prise en charge.", "previous_approved": "",
    }).status_code == 200

    assert start(client, name="Villa Rebeca", initials="VIL").status_code == 200
    assert client.post("/approve", headers=JSON, data=de_approval | {
        "summary": original + " Ergänzt.", "previous_approved": original,
    }).status_code == 200

    # A stale submission must produce neither a text change nor an extra signature.
    assert client.post("/approve", headers=JSON, data=de_approval | {
        "summary": "Veraltete Änderung", "previous_approved": original,
    }).status_code == 409
    with closing(dashboard.connect()) as db:
        assert [tuple(row) for row in db.execute(
            "SELECT kind, initials FROM signatures WHERE case_key=? ORDER BY id", (case,),
        )] == [("de", "FRT"), ("fr", "FRT"), ("de", "VIL")]

    response = client.get("/api/export", query_string={"case": case, "format": format})
    assert response.status_code == 200
    authors = ["Autor", "Villa Rebeca (VIL)", "Frei Thomas (FRT), Rechtsanwalt"]
    if format == "html":
        ending = f"<h2>{authors[0]}</h2><p>{authors[1]}</p><p>{authors[2]}</p><hr></body></html>"
        assert response.get_data(as_text=True).endswith(ending)
    elif format == "docx":
        with ZipFile(BytesIO(response.data)) as archive:
            document = ElementTree.fromstring(archive.read("word/document.xml"))
        body = [node.text for node in document.findall(".//{*}t")]
        assert body[-3:] == authors
    else:
        assert response.mimetype == "application/pdf"
        # The built-in renderer writes uncompressed text operators with escaped parentheses.
        text = response.data.decode("cp1252")
        lines = [line.split(" Tf (", 1)[1].rsplit(") Tj", 1)[0] for line in text.splitlines()
                 if " Tf (" in line and " Tf ()" not in line]
        assert lines[-3:] == [line.replace("(", r"\(").replace(")", r"\)") for line in authors]


def test_session_secret_is_created_once_and_private(tmp_path, monkeypatch):
    monkeypatch.delenv("SUVA_SECRET_KEY", raising=False)
    monkeypatch.setattr(dashboard.app, "secret_key", None)
    monkeypatch.setitem(dashboard.app.config, "DATABASE", tmp_path / "review.sqlite")
    dashboard.ensure_secret_key()
    first = dashboard.app.secret_key
    assert stat.S_IMODE((tmp_path / "session-secret").stat().st_mode) == 0o600 and len(first) == 64
    monkeypatch.setattr(dashboard.app, "secret_key", None)
    dashboard.ensure_secret_key()
    assert dashboard.app.secret_key == first


def test_an_older_review_database_gains_the_signer_columns(tmp_path):
    path = tmp_path / "old.sqlite"
    with closing(sqlite3.connect(path)) as old, old:
        old.execute("""CREATE TABLE triage_feedback (case_key TEXT PRIMARY KEY, status TEXT NOT NULL,
                       reason TEXT NOT NULL DEFAULT '', rule_points REAL,
                       saved_at TEXT NOT NULL DEFAULT (datetime('now')))""")
        old.execute("INSERT INTO triage_feedback (case_key, status) VALUES ('8C_1/2026@2026-10-01', 'Relevant')")
    with closing(review_store.connect(path)) as db, closing(review_store.connect(path)) as again:
        feedback = review_store.triage_feedback(db, "8C_1/2026@2026-10-01")
        assert feedback["status"] == "Relevant" and feedback["signer"] is None
        review_store.add_missing_columns(again, "triage_feedback", {"signer_name": "TEXT"})  # already there
