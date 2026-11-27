"""Local Suva judgment review dashboard and batch commands.

Judgements, their parse and the rules score come from the suva knowledge base
(`python -m suva fetch`, `parse`, `score`). This app stores only review state:
summary drafts, approvals, audio briefings and cases a reviewer added by hand.
"""

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
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, redirect, request, send_file, send_from_directory, url_for

import briefing
import conversation
import review_kb
from bger import CASE_ID, ROOT, model_config
from summaries import VERSION, draft, make_facts
from suva.config import load_settings

PAGE = 30
_settings = load_settings()
app = Flask(__name__)
app.config.update(MAX_CONTENT_LENGTH=4 * 1024 * 1024,
                  TRUSTED_HOSTS=["localhost", "127.0.0.1"],
                  DATABASE=_settings.data_dir / "review.sqlite",
                  KB=_settings.sqlite_path,
                  TODAY=None)  # reference date of the challenge window (None = today)
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
    """The app's review-state database (drafts, approvals, briefings, added cases)."""
    path = Path(app.config["DATABASE"])
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=20)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS results (
            case_key TEXT NOT NULL, kind TEXT NOT NULL, version TEXT NOT NULL,
            payload TEXT, error TEXT, approved TEXT,
            PRIMARY KEY (case_key, kind, version)
        );
        CREATE TABLE IF NOT EXISTS briefings (
            cache_key TEXT PRIMARY KEY, script TEXT, evidence TEXT, mp3 BLOB, error TEXT
        );
        CREATE TABLE IF NOT EXISTS review_cases (
            case_key TEXT PRIMARY KEY, added_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    return db


def kb():
    return review_kb.open_kb(app.config["KB"])


def window():
    return review_kb.window_for(app.config["TODAY"])


def load_record(key):
    if not isinstance(key, str):
        return None
    with closing(kb()) as repo:
        return review_kb.get_record(repo, key)


def valid_date(value):
    return date.fromisoformat(value) if value else None


def safe_url(value):
    if value:
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            return value
    return None


def version(record, model):
    digest = hashlib.sha256(record["full_text"].encode()).hexdigest()
    return f"{VERSION}:{model}:{digest}"


def current_model():
    try:
        return model_config()["LLM_NAME"]
    except ValueError:
        return "unconfigured"


def get_result(db, record, kind, model):
    return db.execute("SELECT * FROM results WHERE case_key=? AND kind=? AND version=?",
                      (record["key"], kind, version(record, model))).fetchone()


app.register_blueprint(conversation.routes(load_record))


def process(key, kind):
    record = load_record(key)
    if record is None:
        return False
    with closing(connect()) as db:
        model = current_model()
        existing = get_result(db, record, kind, model)
        if existing and existing["payload"]:
            return True
        payload = None
        error = None
        try:
            config = model_config()
            if kind == "facts":
                payload = make_facts(config, record["full_text"])
            else:
                if not process(key, "facts"):
                    raise ValueError("The factual brief failed. Retry summary generation.")
                facts = json.loads(get_result(db, record, "facts", model)["payload"])
                approved = db.execute("""
                    SELECT approved FROM results WHERE kind=? AND case_key<>? AND approved IS NOT NULL
                    ORDER BY rowid DESC LIMIT 100
                """, (kind, key))
                payload = draft(config, record, facts, kind, [row[0][:6000] for row in approved])
        except (OSError, ValueError, TypeError, KeyError) as exc:
            # Error responses and exception arguments can contain provider/request data.
            error = (f"Provider HTTP {exc.code}" if hasattr(exc, "code") else
                     "Missing model configuration" if model == "unconfigured" else
                     "Request failed or returned invalid source evidence")
            print(f"{key} {kind}: {error}", file=sys.stderr, flush=True)
        with db:
            db.execute("""INSERT INTO results (case_key, kind, version, payload, error)
                VALUES (?, ?, ?, ?, ?) ON CONFLICT(case_key,kind,version)
                DO UPDATE SET payload=excluded.payload, error=excluded.error""",
                       (key, kind, version(record, model),
                        json.dumps(payload, ensure_ascii=False) if payload else None, error))
        return payload is not None


def enqueue(key, kinds):
    with pending_lock:
        tasks = [kind for kind in kinds if (key, kind) not in pending]
        pending.update((key, kind) for kind in tasks)

    def run():
        for kind in tasks:
            try:
                process(key, kind)
            except sqlite3.Error:
                app.logger.exception("Could not save %s for %s", kind, key)
            finally:
                with pending_lock:
                    pending.discard((key, kind))
    if tasks:
        pool.submit(run)


def present(db, record, model):
    result = dict(record)
    for kind in ("de", "fr"):
        row = get_result(db, record, kind, model)
        with pending_lock:
            active = (record["key"], kind) in pending
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


def queue_row(record):
    top = next((f["explanation"] for f in record["fired"] if f["points"] > 0), None)
    return {key: record[key] for key in
            ("key", "docket", "title", "publication_date", "decision_date", "score", "status")} | {"issue": top}


@app.get("/api/dashboard")
def dashboard_data():
    lang = "fr" if request.args.get("lang") == "fr" else "de"
    selected_key = request.args.get("case")
    current = window()
    with closing(connect()) as db:
        extra = [row[0] for row in db.execute("SELECT case_key FROM review_cases")]
        with closing(kb()) as repo:
            digest = review_kb.current_rules_hash()
            records = review_kb.window_records(repo, current, extra, digest)
            selected = review_kb.get_record(repo, selected_key, digest) if selected_key else None
        model = current_model()
        if selected:
            selected = present(db, selected, model)
        audio = briefing_status(db, selected, lang, model) if selected else {}
    published_from = current.published_from.isoformat()
    dates = sorted(r["publication_date"] for r in records
                   if r["publication_date"] and published_from <= r["publication_date"] <= current.end.isoformat())
    query = request.args.get("q", "").strip().casefold()
    status = request.args.get("status", "")
    rows = [r for r in records
            if query in f"{r['docket']} {r['title']} {' '.join(f['explanation'] for f in r['fired'])}".casefold()
            and (not status or status == r["status"])]
    sort = request.args.get("sort", "score")
    rows.sort(key=lambda r: (r["publication_date"] or "", r["decision_date"] or "", r["docket"]), reverse=True)
    if sort != "date":
        rows.sort(key=lambda r: r["score"] if r["score"] is not None else -1, reverse=True)
    page = request.args.get("page", 1, type=int)
    page = min(max(page, 1), max(1, (len(rows) + PAGE - 1) // PAGE))
    response = jsonify(
        rows=[queue_row(r) for r in rows[(page - 1) * PAGE:page * PAGE]],
        selected=selected, count=len(records), total=len(rows),
        scored=sum(r["score"] is not None for r in records),
        shortlist=sum(r["status"] in ("Relevant", "Review") for r in records),
        stale=sum(r["status"] == "Stale" for r in records),
        unknown_publication=sum(not r["publication_date"] for r in records),
        window=(published_from, current.end.isoformat()),
        dates=(dates[0], dates[-1]) if dates else None, model=model,
        query=query, status=status, sort=sort, page=page,
        message=request.args.get("message"), lang=lang, audio=audio,
        active=bool(audio.get("active") or selected and any(
            selected[k]["active"] for k in ("de", "fr"))),
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def prepare_briefing(key, lang):
    config = model_config()
    record = load_record(key)
    if not record:
        raise ValueError("Judgment not found.")
    with closing(connect()) as db:
        ctx, api_key, voice = briefing_context(db, record, lang, config["LLM_NAME"])
        summary = get_result(db, record, lang, config["LLM_NAME"])
        if not summary or not summary["approved"]:
            raise ValueError("Approve this language’s summary before generating audio.")
        if not ctx or not api_key:
            raise ValueError("Configure the ElevenLabs API key and voice for this language.")
    cache_key, approved = ctx
    return cache_key, (connect, app.config["DATABASE"], cache_key, record, approved, lang, config, api_key, voice)


@app.post("/briefing")
def queue_briefing():
    key, lang = request.form["case"], request.form["lang"]
    if lang not in briefing.LANGUAGES:
        abort(400)
    message = None
    try:
        cache_key, args = prepare_briefing(key, lang)
    except (OSError, ValueError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "Could not load briefing configuration."
    else:
        task = ("briefing", cache_key)

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
        return action_result(key, message, 400 if message else 202)
    return redirect(url_for("dashboard", case=key, lang=lang, message=message,
                            _anchor="case-briefing"), code=303)


@app.get("/briefing/<cache_key>.mp3")
def play_briefing(cache_key):
    lang = request.args.get("lang")
    if lang not in briefing.LANGUAGES:
        abort(404)
    record = load_record(request.args.get("case"))
    if not record:
        abort(404)
    with closing(connect()) as db:
        ctx, _, _ = briefing_context(db, record, lang, current_model())
        if not ctx or ctx[0] != cache_key:
            abort(404)
        row = db.execute("SELECT mp3 FROM briefings WHERE cache_key=?", (cache_key,)).fetchone()
        if not row or not row["mp3"]:
            abort(404)
        response = send_file(BytesIO(row["mp3"]), mimetype="audio/mpeg",
                             conditional=True, etag=cache_key)
        response.headers["Cache-Control"] = "private, no-store"
        return response


def wants_json():
    return request.accept_mimetypes.best == "application/json"


def action_result(key, message=None, status=200):
    return jsonify(case=key, message=message), status


def jump(key=None, message=None):
    if wants_json():
        return action_result(key, message, 200 if key else 400)
    return redirect(url_for("dashboard", case=key, message=message), code=303)


@app.post("/run")
def run_case():
    key = request.form["case"]
    if request.form["action"] != "summarize":
        abort(400)
    if load_record(key) is None:
        abort(404)
    enqueue(key, ("de", "fr"))
    return jump(key)


@app.post("/add")
def add_case():
    docket = request.form.get("docket", "").strip()
    upload = request.files.get("text")
    try:
        if not CASE_ID.fullmatch(docket):
            raise ValueError("Enter a Swiss case ID such as 8C_123/2026.")
        decided = valid_date(request.form.get("decision_date"))
        with closing(kb()) as repo:
            if upload and upload.filename:
                key = review_kb.add_text(
                    repo, docket, upload.read().decode("utf-8-sig"), decided,
                    valid_date(request.form.get("publication_date")),
                    safe_url(request.form.get("source_url")))
            else:
                keys = review_kb.find_keys(repo, docket, decided)
                if not keys:
                    raise ValueError("That judgment is not in the knowledge base. Run `python -m suva fetch` "
                                     "or upload its original text.")
                if len(keys) > 1:
                    dates = ", ".join(k.partition("@")[2] for k in keys)
                    raise ValueError(f"{docket} has several decisions ({dates}). Enter the decision date.")
                key = keys[0]
        with closing(connect()) as db, db:
            db.execute("INSERT OR IGNORE INTO review_cases (case_key) VALUES (?)", (key,))
    except ValueError as exc:
        message = str(exc) if str(exc) else "Could not add that judgment."
        return jump(message=message)
    except (OSError, UnicodeDecodeError):
        return jump(message="Could not add that judgment. Upload a UTF-8 text file.")
    return jump(key, "Judgment added.")


@app.post("/approve")
def approve():
    key, lang = request.form["case"], request.form["lang"]
    if lang not in {"de", "fr"}:
        abort(400)
    summary = request.form["summary"].strip()
    if not summary or len(summary) > 16000:
        abort(400)
    record = load_record(key)
    if record is None:
        abort(404)
    with closing(connect()) as db, db:
        row = get_result(db, record, lang, current_model())
        if not row or not row["payload"]:
            abort(404)
        db.execute("UPDATE results SET approved=? WHERE case_key=? AND kind=? AND version=?",
                   (summary, key, lang, row["version"]))
    if wants_json():
        return action_result(key, "Summary approved.")
    return redirect(url_for("dashboard", case=key, lang=lang, message="Summary approved."), code=303)


def summarize_top(limit, workers):
    """Draft DE and FR summaries for the best-scored judgements of the window."""
    with closing(kb()) as repo:
        records = review_kb.window_records(repo, window())
    ranked = sorted((r for r in records if r["status"] in ("Relevant", "Review")),
                    key=lambda r: -r["score"])[:limit]
    stale = sum(r["status"] == "Stale" for r in records)
    if stale:
        print(f"note: {stale} judgements have stale scores: run `python -m suva score` first", file=sys.stderr)

    def summarize(row):
        return sum(not process(row["key"], lang) for lang in ("de", "fr"))

    failed = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for index, errors in enumerate(executor.map(summarize, ranked), 1):
            failed += errors
            print(f"{index}/{len(ranked)} cases; {failed} tasks need retry", flush=True)
    print(f"{len(ranked)} cases processed; {failed} tasks need retry.")
    return int(bool(failed))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--today", type=date.fromisoformat,
                        help="reference date of the challenge window (YYYY-MM-DD, default: today)")
    commands = parser.add_subparsers(dest="command", required=True)
    server = commands.add_parser("serve")
    server.add_argument("--port", type=int, default=5050)
    top = commands.add_parser("summarize-top", help="draft DE/FR summaries for the best-scored judgements")
    top.add_argument("--limit", type=int, default=10)
    top.add_argument("--workers", default=4, type=int, choices=range(1, 9))
    audio = commands.add_parser("briefing", help="Pre-generate audio for an approved summary")
    audio.add_argument("--case", required=True, help="judgement key, e.g. 8C_354/2026@2026-09-01")
    audio.add_argument("--lang", required=True, choices=("de", "fr", "both"))
    args = parser.parse_args()
    app.config["TODAY"] = args.today
    if args.command == "serve":
        app.run(host="127.0.0.1", port=args.port)
    elif args.command == "summarize-top":
        return summarize_top(args.limit, args.workers)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
