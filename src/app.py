"""Local Suva judgment review dashboard and batch commands.

Judgements, their parse and the rules score come from the suva knowledge base
(`python -m suva fetch`, `parse`, `score`). This app stores only review state:
summary drafts, approvals, audio briefings and cases a reviewer added by hand.
"""

import argparse
from contextlib import closing
from datetime import date
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
import jobs
import review_kb
import review_store
from bger import ROOT, model_config
from jobs import connect, kb, load_record, window
from suva.config import configure_logging, load_settings
from suva.sources.base import DOCKET_RE

PAGE = 30
_settings = load_settings()
app = Flask(__name__)
app.config.update(MAX_CONTENT_LENGTH=4 * 1024 * 1024,
                  TRUSTED_HOSTS=["localhost", "127.0.0.1"],
                  DATABASE=_settings.data_dir / "review.sqlite",
                  KB=_settings.sqlite_path,
                  TODAY=None)  # reference date of the challenge window (None = today)
jobs.settings = app.config  # the jobs read DATABASE, KB and TODAY from here
queue_read_lock = threading.Lock()


@app.before_request
def reject_cross_site_writes():
    if request.method == "POST":
        origin = request.headers.get("Origin")
        if (origin and origin.rstrip("/") != request.host_url.rstrip("/")
                or request.headers.get("Sec-Fetch-Site") == "cross-site"):
            abort(403)


def valid_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        raise ValueError("Enter dates as YYYY-MM-DD.") from None


def safe_url(value):
    if value:
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            return value
    return None


app.register_blueprint(conversation.routes(load_record))


def present(db, record, config):
    result = dict(record)
    for kind in ("de", "fr"):
        # Read the pending flag before the row: a job commits its row before clearing the flag, so a response
        # never says "not active" while still missing the result it just committed.
        active = jobs.is_pending((record["key"], kind))
        row = jobs.get_result(db, record, kind, config)
        result[kind] = {
            "version": row["version"] if row and row["payload"] else None,
            "data": json.loads(row["payload"]) if row and row["payload"] else {},
            "error": row["error"] if row else None,
            "approved": row["approved"] if row else None,
            "active": active,
        }
    return result


def briefing_context(db, record, lang, config):
    api_key, voice = briefing.voice_config(lang)
    summary = jobs.get_result(db, record, lang, config)
    ctx = briefing.context(record, summary, config["LLM_NAME"], jobs.generation_version(record, config), lang, voice)
    return ctx, api_key, voice


def briefing_status(db, record, lang, config):
    ctx, api_key, _ = briefing_context(db, record, lang, config)
    if not ctx:
        return {}
    key, _ = ctx
    active = jobs.is_pending(("briefing", key))  # before the row, as in present()
    row = review_store.briefing_status(db, key)
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
        extra = review_store.added_cases(db)
        with closing(kb()) as repo:
            digest = review_kb.current_rules_hash()
            # Concurrent SQLite row scans contend heavily in the Python driver.
            with queue_read_lock:
                records = review_kb.window_records(repo, current, extra, digest)
            selected = review_kb.get_record(repo, selected_key, digest) if selected_key else None
        config = jobs.current_config()
        if selected:
            selected = present(db, selected, config)
        audio = briefing_status(db, selected, lang, config) if selected else {}
    published_from = current.published_from.isoformat()
    dates = sorted(r["publication_date"] for r in records
                   if r["publication_date"] and published_from <= r["publication_date"] <= current.end.isoformat())
    query = request.args.get("q", "").strip().casefold()
    status = request.args.get("status", "")
    rows = review_kb.filter_queue(records, query, status)
    sort = request.args.get("sort", "score")
    rows.sort(key=lambda r: (r["publication_date"] or "", r["decision_date"] or "", r["docket"]), reverse=True)
    if sort != "date":
        rows = review_kb.rank(rows)
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
        dates=(dates[0], dates[-1]) if dates else None, model=config["LLM_NAME"],
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
        ctx, api_key, voice = briefing_context(db, record, lang, config)
        summary = jobs.get_result(db, record, lang, config)
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
        app.logger.info("briefing_rejected case=%s lang=%s reason=%r", key, lang, message)
    else:
        jobs.enqueue_briefing(key, lang, cache_key, args)
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
        ctx, _, _ = briefing_context(db, record, lang, jobs.current_config())
        if not ctx or ctx[0] != cache_key:
            abort(404)
        audio = review_store.briefing_audio(db, cache_key)
        if not audio:
            abort(404)
        response = send_file(BytesIO(audio), mimetype="audio/mpeg",
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
    jobs.enqueue(key, ("de", "fr"))
    return jump(key)


@app.post("/add")
def add_case():
    docket = request.form.get("docket", "").strip()
    upload = request.files.get("text")
    try:
        if not DOCKET_RE.fullmatch(docket):
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
        with closing(connect()) as db:
            review_store.add_case(db, key)
    except (OSError, UnicodeDecodeError) as exc:
        app.logger.info("add_case_rejected docket=%r error=%s", docket[:40], type(exc).__name__)
        return jump(message="Could not add that judgment. Upload a UTF-8 text file.")
    except ValueError as exc:
        message = str(exc) if str(exc) else "Could not add that judgment."
        app.logger.info("add_case_rejected docket=%r reason=%r", docket[:40], message)
        return jump(message=message)
    app.logger.info("case_added case=%s upload=%s", key, bool(upload and upload.filename))
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
    with closing(connect()) as db:
        row = jobs.get_result(db, record, lang, jobs.current_config())
        if not row or not row["payload"] or request.form.get("version") != row["version"]:
            app.logger.info("approve_conflict case=%s lang=%s reason=%s", key, lang,
                            "no_draft" if not row or not row["payload"] else "version_changed")
            return action_result(key, "The draft changed. Reload it and review it before approving.", 409)
        # Only overwrite the approval this page saw: a second tab saving the same draft version must reload
        # instead of silently replacing the first tab's edit. The condition makes the check atomic.
        if not review_store.approve(db, key, lang, row["version"], summary,
                                    request.form.get("previous_approved", "")):
            app.logger.info("approve_conflict case=%s lang=%s reason=approval_changed", key, lang)
            return action_result(key, "Another approval was saved for this draft. Reload it and review it "
                                      "before approving.", 409)
    app.logger.info("summary_approved case=%s lang=%s chars=%d", key, lang, len(summary))
    if wants_json():
        return action_result(key, "Summary approved.")
    return redirect(url_for("dashboard", case=key, lang=lang, message="Summary approved."), code=303)


def startup_problems():
    """What the review app needs before serving: (required, optional) problems, never secret values."""
    required, optional = [], []
    try:
        model_config()
    except ValueError as exc:
        required.append(f"LLM: {exc} (see .env.example; check with `python -m suva llm-ping`)")
    if not (FRONTEND / "index.html").is_file():
        required.append("frontend not built: run `npm --prefix src/frontend ci && npm --prefix src/frontend run build`")
    kb_path = Path(app.config["KB"])
    if not kb_path.is_file():
        required.append(f"knowledge base {kb_path} not found: run `python -m suva init-db`, `fetch`, "
                        "`parse` and `score`")
    else:
        try:  # read-only: the check must never create or change the file
            with closing(sqlite3.connect(f"{kb_path.resolve().as_uri()}?mode=ro", uri=True)) as probe:
                probe.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
        except sqlite3.Error as exc:
            required.append(f"knowledge base {kb_path} is not a readable SQLite database ({exc}): restore it "
                            "or run `python -m suva init-db` with a new DATABASE_URL")
    for lang in briefing.LANGUAGES:
        api_key, voice = briefing.voice_config(lang)
        if not api_key or not voice:
            optional.append(f"ElevenLabs {lang}: set ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID_{lang.upper()} "
                            "to enable audio and voice questions")
    return required, optional


def check_startup(strict):
    """Log the configuration once at startup; with --strict, refuse to serve without the required parts."""
    required, optional = startup_problems()
    config = jobs.current_config()
    app.logger.info("startup_config kb=%s review_db=%s model=%s llm_host=%s today=%s", app.config["KB"],
                    app.config["DATABASE"], config["LLM_NAME"], urlsplit(config.get("LLM_BASE_URL", "")).hostname,
                    app.config["TODAY"] or "today")
    for problem in required:
        app.logger.error("startup_check_failed required=yes problem=%r", problem)
    for problem in optional:
        app.logger.warning("startup_check_failed required=no problem=%r", problem)
    if required and strict:
        app.logger.error("startup_refused problems=%d (drop --strict to serve anyway)", len(required))
        return False
    if not required:
        app.logger.info("startup_check_ok optional_missing=%d", len(optional))
    return True


def main():
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--today", type=date.fromisoformat,
                        help="reference date of the challenge window (YYYY-MM-DD, default: today)")
    commands = parser.add_subparsers(dest="command", required=True)
    server = commands.add_parser("serve")
    server.add_argument("--port", type=int, default=5050)
    server.add_argument("--strict", action="store_true",
                        help="exit if the LLM config, frontend build or knowledge base is missing")
    top = commands.add_parser("summarize-top", help="draft DE/FR summaries for the best-scored judgements")
    top.add_argument("--limit", type=int, default=10)
    top.add_argument("--workers", default=4, type=int, choices=range(1, 9))
    audio = commands.add_parser("briefing", help="Pre-generate audio for an approved summary")
    audio.add_argument("--case", required=True, help="judgement key, e.g. 8C_354/2026@2026-09-01")
    audio.add_argument("--lang", required=True, choices=("de", "fr", "both"))
    args = parser.parse_args()
    app.config["TODAY"] = args.today
    if args.command == "serve":
        if not check_startup(args.strict):
            return 2
        app.run(host="127.0.0.1", port=args.port)
    elif args.command == "summarize-top":
        return jobs.summarize_top(args.limit, args.workers)
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
                    error = review_store.briefing_error(db, key)
                print(f"{lang}: {error}", file=sys.stderr)
                failed = True
        return int(failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
