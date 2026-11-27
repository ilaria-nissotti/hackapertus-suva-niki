"""Local Suva judgment review dashboard and batch commands."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import sys
import threading
from urllib.parse import quote, urlsplit

from flask import Flask, abort, jsonify, redirect, request, send_file, send_from_directory, url_for

import briefing
import conversation
from bger import CASE_ID, ROOT, fetch_opencaselaw, iter_judgments, model_config
from triage import CUTOFF, HIGH_SCORE, VERSION, classify, draft, make_facts

app = Flask(__name__)
app.config.update(MAX_CONTENT_LENGTH=4 * 1024 * 1024,
                  TRUSTED_HOSTS=["localhost", "127.0.0.1"],
                  DATABASE=ROOT / ".suva-work" / "judgments.sqlite")
pool = ThreadPoolExecutor(max_workers=1)
pending = set()
pending_lock = threading.Lock()


@app.before_request
def reject_cross_site_writes():
    if request.method == "POST":
        origin = request.headers.get("Origin")
        if (origin and origin.rstrip("/") != request.host_url.rstrip("/")
                or request.headers.get("Sec-Fetch-Site") == "cross-site"):
            abort(403)


def connect():
    path = Path(app.config["DATABASE"])
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=20)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS judgments (
            docket TEXT PRIMARY KEY, title TEXT NOT NULL, source_url TEXT,
            decision_date TEXT, publication_date TEXT, date_source TEXT,
            full_text TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS results (
            docket TEXT NOT NULL, kind TEXT NOT NULL, version TEXT NOT NULL,
            payload TEXT, error TEXT, approved TEXT,
            PRIMARY KEY (docket, kind, version)
        );
        CREATE TABLE IF NOT EXISTS briefings (
            cache_key TEXT PRIMARY KEY, script TEXT, evidence TEXT, mp3 BLOB, error TEXT
        );
    """)
    return db


def valid_date(value):
    return date.fromisoformat(value).isoformat() if value else None


def safe_url(value):
    if value:
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            return value
    return None


def save_judgment(db, item):
    docket = item.get("docket_number") or item.get("docket")
    if not isinstance(docket, str) or not CASE_ID.fullmatch(docket):
        raise ValueError("A Swiss case ID such as 8C_123/2026 is required")
    source = item.get("full_text")
    if not isinstance(source, str) or not source.strip():
        raise ValueError(f"{docket} has no full text")
    publication = valid_date(item.get("publication_date"))
    db.execute("""
        INSERT INTO judgments VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(docket) DO UPDATE SET title=excluded.title,
          source_url=coalesce(excluded.source_url, judgments.source_url),
          decision_date=coalesce(excluded.decision_date, judgments.decision_date),
          publication_date=coalesce(excluded.publication_date, judgments.publication_date),
          date_source=coalesce(excluded.date_source, judgments.date_source),
          full_text=excluded.full_text
    """, (docket, item.get("title") or docket,
          safe_url(item.get("source_url") or item.get("canonical_url")),
          valid_date(item.get("decision_date")), publication,
          (item.get("date_source") or "OpenCaseLaw") if publication else None, source))
    return docket


def version(record, model):
    digest = hashlib.sha256(record["full_text"].encode()).hexdigest()
    return f"{VERSION}:{model}:{digest}"


def current_model():
    try:
        return model_config()["LLM_NAME"]
    except ValueError:
        return "unconfigured"


def get_result(db, record, kind, model):
    return db.execute("SELECT * FROM results WHERE docket=? AND kind=? AND version=?",
                      (record["docket"], kind, version(record, model))).fetchone()


app.register_blueprint(conversation.routes(connect, get_result))


def process(docket, kind):
    with closing(connect()) as db:
        record = db.execute("SELECT * FROM judgments WHERE docket=?", (docket,)).fetchone()
        if record is None:
            return False
        model = current_model()
        existing = get_result(db, record, kind, model)
        if existing and existing["payload"]:
            return True
        payload = None
        error = None
        try:
            config = model_config()
            if kind == "triage":
                payload = classify(config, record)
            elif kind == "facts":
                payload = make_facts(config, record["full_text"])
            else:
                if not process(docket, "facts"):
                    raise ValueError("The factual brief failed. Retry summary generation.")
                facts = json.loads(get_result(db, record, "facts", model)["payload"])
                approved = db.execute("""
                    SELECT approved FROM results WHERE kind=? AND docket<>? AND approved IS NOT NULL
                    ORDER BY rowid DESC LIMIT 100
                """, (kind, docket))
                payload = draft(config, record, facts, kind, [row[0][:6000] for row in approved])
        except (OSError, ValueError, TypeError, KeyError) as exc:
            # Error responses and exception arguments can contain provider/request data.
            error = (f"Provider HTTP {exc.code}" if hasattr(exc, "code") else
                     "Missing model configuration" if model == "unconfigured" else
                     "Request failed or returned invalid source evidence")
            print(f"{docket} {kind}: {error}", file=sys.stderr, flush=True)
        with db:
            db.execute("""INSERT INTO results (docket, kind, version, payload, error)
                VALUES (?, ?, ?, ?, ?) ON CONFLICT(docket,kind,version)
                DO UPDATE SET payload=excluded.payload, error=excluded.error""",
                       (docket, kind, version(record, model),
                        json.dumps(payload, ensure_ascii=False) if payload else None, error))
        return payload is not None


def enqueue(docket, kinds):
    with pending_lock:
        tasks = [kind for kind in kinds if (docket, kind) not in pending]
        pending.update((docket, kind) for kind in tasks)

    def run():
        for kind in tasks:
            try:
                process(docket, kind)
            except sqlite3.Error:
                app.logger.exception("Could not save %s for %s", kind, docket)
            finally:
                with pending_lock:
                    pending.discard((docket, kind))
    if tasks:
        pool.submit(run)


def present(db, record, model):
    result = dict(record)
    for kind in ("triage", "de", "fr"):
        row = get_result(db, record, kind, model)
        with pending_lock:
            active = (record["docket"], kind) in pending
        result[kind] = {
            "data": json.loads(row["payload"]) if row and row["payload"] else {},
            "error": row["error"] if row else None,
            "approved": row["approved"] if row else None,
            "active": active,
        }
    return result


def briefing_context(db, record, lang, model):
    api_key, voice = briefing.voice_config(lang)
    summary = get_result(db, record, lang, model)
    ctx = briefing.context(record, summary, model, version(record, model), lang, voice)
    return ctx, api_key, voice


def briefing_status(db, record, lang, model):
    ctx, api_key, _ = briefing_context(db, record, lang, model)
    if not ctx:
        return {}
    key, _ = ctx
    row = db.execute("""SELECT script, evidence, mp3 IS NOT NULL AS ready, error
                        FROM briefings WHERE cache_key=?""", (key,)).fetchone()
    with pending_lock:
        active = ("briefing", key) in pending
    return {"key": key, "active": active, "configured": bool(api_key),
            "ready": bool(row and row["ready"]), "error": row["error"] if row else None,
            "script": row["script"] if row else None,
            "excerpts": json.loads(row["evidence"]) if row and row["evidence"] else []}


FRONTEND = Path(__file__).resolve().parent / "frontend" / "dist"


@app.get("/")
def dashboard():
    if not (FRONTEND / "index.html").is_file():
        return "Build the frontend with npm --prefix src/frontend ci and npm --prefix src/frontend run build.", 503
    return send_from_directory(FRONTEND, "index.html")


@app.get("/assets/<path:filename>")
def frontend_asset(filename):
    return send_from_directory(FRONTEND / "assets", filename)


@app.get("/conversation-portrait")
def conversation_portrait():
    directory = ROOT / ".suva-work"
    for filename in ("avatar.png", "avatar.jpg"):
        if (directory / filename).is_file():
            return send_from_directory(directory, filename, max_age=0)
    return "", 404


@app.get("/api/dashboard")
def dashboard_data():
    lang = "fr" if request.args.get("lang") == "fr" else "de"
    selected_docket = request.args.get("case")
    with closing(connect()) as db:
        model = current_model()
        records = [present(db, row, model) for row in db.execute("SELECT * FROM judgments")]
        selected = next((r for r in records if r["docket"] == selected_docket), None)
        audio = briefing_status(db, selected, lang, model) if selected else {}
    dates = sorted(r["publication_date"] for r in records
                   if r["publication_date"] and "2026-04-01" <= r["publication_date"] <= "2026-06-30")
    query = request.args.get("q", "").strip().casefold()
    status = request.args.get("status", "")
    rows = []
    for r in records:
        triage = r["triage"]
        score = triage["data"].get("score")
        r["score"] = score
        r["status"] = ("Queued" if triage["active"] else "Retry" if triage["error"] else
                       "Unscored" if score is None else "Relevant" if score >= HIGH_SCORE else
                       "Review" if score >= CUTOFF else "Low relevance")
        if (query in f"{r['docket']} {r['title']} {triage['data'].get('issue','')}".casefold()
                and (not status or status == r["status"])):
            rows.append(r)
    sort = request.args.get("sort", "score")
    rows.sort(key=lambda r: (r["publication_date"] or "", r["docket"]), reverse=True)
    if sort != "date":
        rows.sort(key=lambda r: r["score"] if r["score"] is not None else -1, reverse=True)
    page = request.args.get("page", 1, type=int)
    page = min(max(page, 1), max(1, (len(rows) + 29) // 30))
    columns = ("docket", "title", "publication_date", "score", "status")
    queue = [{**{key: row[key] for key in columns}, "issue": row["triage"]["data"].get("issue")}
             for row in rows[(page - 1) * 30:page * 30]]
    response = jsonify(
        rows=queue, selected=selected, count=len(records), total=len(rows),
        scored=sum(r["score"] is not None for r in records),
        shortlist=sum(r["score"] is not None and r["score"] >= CUTOFF for r in records),
        dates=(dates[0], dates[-1]) if dates else None, model=model,
        query=query, status=status, sort=sort, page=page,
        message=request.args.get("message"), lang=lang, audio=audio,
        active=bool(audio.get("active") or selected and any(
            selected[k]["active"] for k in ("triage", "de", "fr"))),
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def prepare_briefing(docket, lang):
    config = model_config()
    with closing(connect()) as db:
        record = db.execute("SELECT * FROM judgments WHERE docket=?", (docket,)).fetchone()
        if not record:
            raise ValueError("Judgment not found.")
        ctx, api_key, voice = briefing_context(db, record, lang, config["LLM_NAME"])
        summary = get_result(db, record, lang, config["LLM_NAME"])
        if not summary or not summary["approved"]:
            raise ValueError("Approve this language’s summary before generating audio.")
        if not ctx or not api_key:
            raise ValueError("Configure the ElevenLabs API key and voice for this language.")
    key, approved = ctx
    return key, (connect, app.config["DATABASE"], key, record, approved, lang, config, api_key, voice)


@app.post("/briefing")
def queue_briefing():
    docket, lang = request.form["docket"], request.form["lang"]
    if lang not in briefing.LANGUAGES:
        abort(400)
    message = None
    try:
        key, args = prepare_briefing(docket, lang)
    except (OSError, ValueError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "Could not load briefing configuration."
    else:
        task = ("briefing", key)

        def run():
            try:
                briefing.generate(*args)
            finally:
                with pending_lock:
                    pending.discard(task)

        with pending_lock:
            if task not in pending:
                pending.add(task)
                pool.submit(run)
    if wants_json():
        return action_result(docket, message, 400 if message else 202)
    return redirect(url_for("dashboard", case=docket, lang=lang, message=message,
                            _anchor="case-briefing"), code=303)


@app.get("/briefing/<key>.mp3")
def play_briefing(key):
    lang = request.args.get("lang")
    if lang not in briefing.LANGUAGES:
        abort(404)
    with closing(connect()) as db:
        record = db.execute("SELECT * FROM judgments WHERE docket=?",
                            (request.args.get("case"),)).fetchone()
        if not record:
            abort(404)
        ctx, _, _ = briefing_context(db, record, lang, current_model())
        if not ctx or ctx[0] != key:
            abort(404)
        row = db.execute("SELECT mp3 FROM briefings WHERE cache_key=?", (key,)).fetchone()
        if not row or not row["mp3"]:
            abort(404)
        response = send_file(BytesIO(row["mp3"]), mimetype="audio/mpeg",
                             conditional=True, etag=key)
        response.headers["Cache-Control"] = "private, no-store"
        return response


def wants_json():
    return request.accept_mimetypes.best == "application/json"


def action_result(docket, message=None, status=200):
    return jsonify(case=docket, message=message), status


def jump(docket=None, message=None):
    if wants_json():
        return action_result(docket, message, 200 if docket else 400)
    return redirect(url_for("dashboard", case=docket, message=message), code=303)


@app.post("/run")
def run_case():
    docket = request.form["docket"]
    action = request.form["action"]
    if action not in {"triage", "summarize"}:
        abort(400)
    with closing(connect()) as db:
        if not db.execute("SELECT 1 FROM judgments WHERE docket=?", (docket,)).fetchone():
            abort(404)
    enqueue(docket, ("triage",) if action == "triage" else ("de", "fr"))
    return jump(docket)


@app.post("/add")
def add_case():
    docket = request.form.get("docket", "").strip()
    upload = request.files.get("text")
    try:
        if not CASE_ID.fullmatch(docket):
            raise ValueError
        if upload and upload.filename:
            item = {"docket": docket, "full_text": upload.read().decode("utf-8-sig"),
                    "source_url": request.form.get("source_url"), "date_source": "Manual",
                    "publication_date": request.form.get("publication_date")}
        else:
            item = fetch_opencaselaw(f"decisions/{quote('bger_' + docket.replace('/', '_'), safe='')}")
            if item.get("docket_number") != docket:
                raise ValueError
        with closing(connect()) as db, db:
            save_judgment(db, item)
    except (ValueError, OSError):
        return jump(message="Could not add that judgment. Check the case ID or upload a UTF-8 text file.")
    return jump(docket, "Judgment added.")


@app.post("/approve")
def approve():
    docket, lang = request.form["docket"], request.form["lang"]
    if lang not in {"de", "fr"}:
        abort(400)
    summary = request.form["summary"].strip()
    if not summary or len(summary) > 16000:
        abort(400)
    with closing(connect()) as db, db:
        model = current_model()
        record = db.execute("SELECT * FROM judgments WHERE docket=?", (docket,)).fetchone()
        if record is None:
            abort(404)
        row = get_result(db, record, lang, model)
        if not row or not row["payload"]:
            abort(404)
        db.execute("UPDATE results SET approved=? WHERE docket=? AND kind=? AND version=?",
                   (summary, docket, lang, row["version"]))
    if wants_json():
        return action_result(docket, "Summary approved.")
    return redirect(url_for("dashboard", case=docket, lang=lang, message="Summary approved."), code=303)


def batch(kind, workers):
    with closing(connect()) as db:
        records = list(db.execute("SELECT * FROM judgments ORDER BY publication_date DESC, docket"))
        if kind == "june":
            model = current_model()
            ranked = []
            for row in records:
                if not (row["publication_date"] or "").startswith("2026-06-"):
                    continue
                result = get_result(db, row, "triage", model)
                if result and result["payload"]:
                    score = json.loads(result["payload"])["score"]
                    if score >= CUTOFF:
                        ranked.append((score, row["docket"]))
            records = [{"docket": docket} for _, docket in sorted(ranked, reverse=True)[:10]]
    kinds = ("de", "fr") if kind == "june" else ("triage",)
    def score_row(row):
        return sum(not process(row["docket"], task) for task in kinds)

    failed = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for index, errors in enumerate(executor.map(score_row, records), 1):
            failed += errors
            if index % 25 == 0 or index == len(records):
                print(f"{index}/{len(records)} cases; {failed} tasks need retry", flush=True)
    print(f"{len(records)} cases processed; {failed} tasks need retry.")
    return int(bool(failed))


def sync():
    args = argparse.Namespace(published_from="2026-04-01", published_to="2026-06-30",
                              limit=None, full_text=False)
    added = failed = 0
    with closing(connect()) as db:
        for row in iter_judgments(args):
            if db.execute("SELECT 1 FROM judgments WHERE docket=? AND date_source='OpenCaseLaw'",
                          (row.get("docket_number"),)).fetchone():
                continue
            try:
                detail = fetch_opencaselaw("decisions/" + quote(row["decision_id"], safe=""))
                with db:
                    save_judgment(db, {**row, **detail})
                added += 1
                if added % 50 == 0:
                    print(f"{added} judgments added", flush=True)
            except (OSError, ValueError) as exc:
                failed += 1
                print(f"{row['decision_id']}: import failed ({type(exc).__name__})",
                      file=sys.stderr, flush=True)
    print(f"{added} judgments added; {failed} need retry.")
    return int(bool(failed))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    server = commands.add_parser("serve")
    server.add_argument("--port", type=int, default=5050)
    importer = commands.add_parser("import")
    importer.add_argument("jsonl", help="JSONL path or - for stdin")
    for name in ("score", "summarize-june"):
        command = commands.add_parser(name)
        command.add_argument("--workers", default=4, type=int, choices=range(1, 9))
    commands.add_parser("sync")
    audio = commands.add_parser("briefing", help="Pre-generate audio for an approved summary")
    audio.add_argument("--case", required=True)
    audio.add_argument("--lang", required=True, choices=("de", "fr", "both"))
    args = parser.parse_args()
    if args.command == "serve":
        app.run(host="127.0.0.1", port=args.port)
    elif args.command == "import":
        stream = sys.stdin if args.jsonl == "-" else open(args.jsonl, encoding="utf-8")
        with closing(stream), closing(connect()) as db, db:
            count = 0
            for count, line in enumerate(filter(str.strip, stream), 1):
                try:
                    save_judgment(db, json.loads(line))
                except ValueError as exc:
                    parser.error(f"Record {count}: {exc}")
        print(f"Imported {count} judgment records.")
    elif args.command == "sync":
        return sync()
    elif args.command == "briefing":
        failed = False
        for lang in briefing.LANGUAGES if args.lang == "both" else (args.lang,):
            try:
                key, inputs = prepare_briefing(args.case, lang)
            except (OSError, ValueError) as exc:
                print(f"{lang}: {exc}", file=sys.stderr)
                failed = True
                continue
            if briefing.generate(*inputs):
                print(f"{args.case} {lang}: briefing ready")
            else:
                with closing(connect()) as db:
                    error = db.execute("SELECT error FROM briefings WHERE cache_key=?", (key,)).fetchone()
                print(f"{lang}: {error['error']}", file=sys.stderr)
                failed = True
        return int(failed)
    else:
        return batch("june" if args.command == "summarize-june" else "triage", args.workers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
