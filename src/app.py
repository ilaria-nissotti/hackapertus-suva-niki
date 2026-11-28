"""Local Suva judgment review dashboard and batch commands.

Judgements, their parse and the rules score come from the suva knowledge base
(`python -m suva fetch`, `parse`, `score`). This app stores only review state:
summary drafts, approvals, audio briefings, cases a reviewer added by hand,
relevance verdicts and rule-weight proposals. Accepting a proposal is the one
action that changes rules/rules.yaml; the window is then rescored.
"""

import argparse
from collections import Counter
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
                  DATASET=_settings.data_dir / "scoring" / "dataset.jsonl",  # labelled dev window (`suva dataset`)
                  TODAY=None)  # reference date of the challenge window (None = today)
jobs.settings = app.config  # the jobs read DATABASE, KB, DATASET and TODAY from here
queue_read_lock = threading.Lock()
weights_lock = threading.Lock()  # one accept at a time: it rewrites rules.yaml
FEEDBACK_THRESHOLD = 30  # new verdicts after which the queue suggests updating the rule weights


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
            selected["triage_feedback"] = review_store.triage_feedback(db, selected["key"])
        audio = briefing_status(db, selected, lang, config) if selected else {}
        feedback = verdict_summary(db)
    rescoring = jobs.is_pending(jobs.RESCORE_TASK)
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
        message=request.args.get("message"), lang=lang, audio=audio, feedback=feedback, rescoring=rescoring,
        active=bool(rescoring or audio.get("active") or selected and any(
            selected[k]["active"] for k in ("de", "fr"))),
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def library_hits(query="", lang=None):
    """Approved summaries matching `query`, with the metadata the library filters on."""
    from suva.parse.citations import extract

    with closing(connect()) as db:
        rows = review_store.search_library(db, query, lang)
    with closing(kb()) as repo:
        meta = review_kb.case_metadata(repo, {row["case_key"] for row in rows})
    hits = []
    for row in rows:
        info = meta.get(row["case_key"])
        if info is None:  # the judgement left the knowledge base
            continue
        # Statutes: laws cited by the judgement or by the approved summary (a summary often cites none).
        laws = set(info["laws"]) | {c.law for c in extract(row["summary"]) if c.kind == "statute" and c.law}
        hits.append({"key": row["case_key"], "language": row["lang"], "approved_at": row["approved_at"],
                     "snippet": row["snippet"], "statutes": sorted(laws)}
                    | {k: v for k, v in info.items() if k != "laws"})
    return hits


# Filter choices offered even before any summary carries them, so every filter can be opened: the
# social-insurance laws Suva works with, the scoring rules that add points, the outcomes the parser derives.
STATUTE_CATALOG = ("UVG", "UVV", "ATSG", "ATSV", "IVG", "IVV", "AHVG", "KVG", "BVG", "MVG", "ELG", "AVIG", "VVG", "BGG")
OUTCOME_CATALOG = ("upheld", "partly_upheld", "dismissed", "not_entered", "written_off", "sent_back")


def library_facets(hits):
    """Every filter value with the number of matching approved summaries (0 when none carries it yet)."""
    from suva.scoring.rules import load_rules

    def count(catalog, values, labels=None):
        found = Counter(values)
        order = list(dict.fromkeys([*catalog, *sorted(found)]))
        return [{"value": v, "count": found[v]} | ({"label": labels[v]} if labels and v in labels else {})
                for v in sorted(order, key=lambda v: (-found[v], order.index(v)))]

    rules = [r for r in load_rules().rules if (r.weight or r.per_match or 0) > 0]
    topics = {r.id: r.explanation for r in rules} | {t["id"]: t["explanation"] for hit in hits for t in hit["topics"]}
    return {
        "statute": count(STATUTE_CATALOG, (law for hit in hits for law in hit["statutes"])),
        "topic": count([r.id for r in rules], (t["id"] for hit in hits for t in hit["topics"]), topics),
        "outcome": count(OUTCOME_CATALOG, (o for hit in hits for o in hit["outcome"])),
        "language": count(("de", "fr"), (hit["language"] for hit in hits)),
    }

@app.get("/api/library")
def library():
    args = request.args
    lang = args.get("language") if args.get("language") in {"de", "fr"} else None
    try:
        start, end = valid_date(args.get("date_from")), valid_date(args.get("date_to"))
    except ValueError as exc:
        return jsonify(message=str(exc)), 400
    hits = library_hits(args.get("q", "").strip(), lang)
    facets = library_facets(hits)
    for name, field in (("statute", "statutes"), ("outcome", "outcome")):
        if args.get(name):
            hits = [hit for hit in hits if args[name] in hit[field]]
    if args.get("topic"):
        hits = [hit for hit in hits if any(t["id"] == args["topic"] for t in hit["topics"])]
    if start or end:
        hits = [hit for hit in hits if hit["decision_date"] and
                (not start or hit["decision_date"] >= start.isoformat()) and
                (not end or hit["decision_date"] <= end.isoformat())]
    response = jsonify(hits=hits, total=len(hits), facets=facets)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/library/ask")
def library_ask():
    import library_ask as qa

    body = request.get_json(silent=True) or {}
    lang = body.get("lang") if body.get("lang") in {"de", "fr"} else "de"
    question = body.get("question")
    if not isinstance(question, str) or not question.strip() or len(question) > qa.MAX_QUESTION:
        return jsonify(message=f"Ask a question of 1–{qa.MAX_QUESTION} characters."), 400
    try:
        config = model_config()
    except ValueError as exc:
        return jsonify(message=str(exc)), 503
    with closing(connect()) as db:
        sources = review_store.search_library(db, qa.search_terms(question), limit=qa.MAX_SOURCES, any_word=True)
    if not sources:
        return jsonify(answer=None, cited=[], message="No approved summary matches this question.")
    try:
        result = qa.answer(config, question.strip(), lang, sources)
    except (OSError, ValueError) as exc:
        app.logger.info("library_ask_failed error=%s", type(exc).__name__)
        return jsonify(message="Could not answer from the library. Try rephrasing the question."), 502
    by_key = {row["case_key"]: row for row in sources}
    return jsonify(answer=result["answer"], cited=[
        {"key": key, "language": by_key[key]["lang"], "snippet": by_key[key]["snippet"]} for key in result["cited"]])


@app.post("/api/case-search")
def case_search_route():
    import case_search

    description = (request.get_json(silent=True) or {}).get("description")
    if not isinstance(description, str) or not description.strip() or len(description) > 4000:
        return jsonify(message="Describe the case in 1–4000 characters (facts, statutes, BGE)."), 400
    with closing(connect()) as db:
        extra = review_store.added_cases(db)
    with closing(kb()) as repo:
        records = review_kb.window_records(repo, window(), extra, include_text=True)
        hits = case_search.search(records, repo.list_citations, repo.list_passages, description.strip())
    return jsonify(hits=hits, searched=len(records))


TRIAGE_STATUSES = ("Relevant", "Not relevant")  # a reviewer's verdict; "Review" is only the rules being unsure


@app.post("/api/triage-feedback")
def save_triage_feedback():
    body = request.get_json(silent=True) or {}
    key, status, reason = body.get("case"), body.get("status"), body.get("reason", "")
    if status not in TRIAGE_STATUSES or not isinstance(reason, str) or len(reason) > 2000:
        return jsonify(message="Choose Relevant or Not relevant; the reason has at most 2000 characters."), 400
    record = load_record(key)
    if record is None:
        return jsonify(message="Judgment not found."), 404
    with closing(connect()) as db:
        # The rule points at the time of the assessment, so a later rules change can be compared against it.
        review_store.save_triage_feedback(db, key, status, reason.strip(), record["points"])
        saved = review_store.triage_feedback(db, key)
    app.logger.info("triage_feedback case=%s status=%s rule_status=%s", key, status, record["status"])
    return jsonify(case=key, triage_feedback=saved, message="Assessment saved.")


# The feedback loop: verdicts -> proposed rule weights -> the expert accepts -> rules.yaml -> rescore.

def verdict_summary(db):
    """Verdict counts, and how many were saved after the last accepted weight update started calculating."""
    last = next(iter(review_store.accepted_proposals(db, limit=1)), None)
    total, per_status, new = review_store.verdict_counts(db, TRIAGE_STATUSES, last["created_at"] if last else None)
    return {"total": total, "relevant": per_status.get("Relevant", 0), "not_relevant": per_status.get("Not relevant", 0),
            "new": new, "threshold": FEEDBACK_THRESHOLD, "last_update": last["decided_at"] if last else None}


def proposal_view(row, current_hash=None):
    if row is None:
        return None
    payload = json.loads(row["payload"]) if row["payload"] else None
    return {"id": row["id"], "status": row["status"], "created_at": row["created_at"],
            "decided_at": row["decided_at"], "error": row["error"], "data": payload,
            # rules.yaml was edited after the calculation: accepting would overwrite those edits
            "rules_changed": bool(payload and current_hash and payload["rules_hash"] != current_hash)}


@app.get("/api/weights")
def weights_status():
    with closing(connect()) as db:
        summary = verdict_summary(db)
        proposal = proposal_view(review_store.open_proposal(db), review_kb.current_rules_hash())
        history = [proposal_view(row) for row in review_store.accepted_proposals(db)]
    response = jsonify(
        verdicts=summary, proposal=proposal, computing=jobs.is_pending(jobs.WEIGHTS_TASK),
        rescoring=jobs.is_pending(jobs.RESCORE_TASK), rescore=jobs.rescore_state,
        history=[{"id": h["id"], "decided_at": h["decided_at"], "verdicts": h["data"]["verdicts"]["used"],
                  "agreement": h["data"]["agreement"], "floor": h["data"]["floor"]} for h in history if h["data"]])
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/weights/propose")
def propose_weights():
    with closing(connect()) as db:
        if not verdict_summary(db)["total"]:
            return jsonify(message="Save at least one relevance verdict before calculating new weights."), 400
    with weights_lock:  # starting supersedes the open proposal, so not while one is being accepted
        proposal_id = jobs.enqueue_weights_proposal()
    if proposal_id is None:
        return jsonify(message="New weights are already being calculated."), 409
    app.logger.info("weights_proposal_started id=%d", proposal_id)
    return jsonify(id=proposal_id, message="Calculating new weights…"), 202


def proposal_id_from_body():
    value = (request.get_json(silent=True) or {}).get("id")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


@app.post("/api/weights/accept")
def accept_weights():
    from suva.scoring.feedback import write_weights
    from suva.scoring.rules import RulesError, load_rules

    proposal_id = proposal_id_from_body()
    with weights_lock, closing(connect()) as db:
        row = review_store.get_proposal(db, proposal_id) if proposal_id is not None else None
        if row is None:
            return jsonify(message="Proposal not found."), 404
        if row["status"] != "pending":
            return jsonify(message="This proposal is no longer open. Reload the page."), 409
        proposal = json.loads(row["payload"])
        try:
            rules = load_rules()
        except (OSError, RulesError) as exc:
            return jsonify(message=f"rules/rules.yaml cannot be read: {exc}"), 409
        if review_kb.current_rules_hash() != proposal["rules_hash"]:
            return jsonify(message="rules/rules.yaml changed after these weights were calculated. "
                                   "Calculate again so the proposal starts from the current rules."), 409
        try:
            write_weights(rules.path, rules, proposal["proposed_weights"], proposal["base"]["proposed"],
                          proposal["floor"]["proposed"], proposal["verdicts"]["used"])
        except (OSError, ValueError) as exc:
            app.logger.warning("weights_write_failed id=%d error=%s", proposal_id, type(exc).__name__)
            return jsonify(message=f"Could not update rules/rules.yaml: {exc}"), 409
        review_store.decide_proposal(db, proposal_id, "accepted")
    jobs.enqueue_rescore()
    app.logger.info("weights_accepted id=%d verdicts=%d floor=%s->%s", proposal_id, proposal["verdicts"]["used"],
                    proposal["floor"]["current"], proposal["floor"]["proposed"])
    return jsonify(message="New weights saved to rules/rules.yaml. Rescoring the queue…")


@app.post("/api/weights/discard")
def discard_weights():
    proposal_id = proposal_id_from_body()
    with weights_lock, closing(connect()) as db:
        if proposal_id is None or not review_store.decide_proposal(db, proposal_id, "discarded"):
            return jsonify(message="This proposal is no longer open. Reload the page."), 409
    app.logger.info("weights_discarded id=%d", proposal_id)
    return jsonify(message="Proposal discarded. The current weights stay as they are.")


@app.get("/api/export")
def export_summary():
    import export

    fmt = request.args.get("format", "html")
    if fmt not in export.FORMATS:
        return jsonify(message="Choose html, docx or pdf."), 400
    record = load_record(request.args.get("case"))
    if record is None:
        return jsonify(message="Judgment not found."), 404
    config = jobs.current_config()
    with closing(connect()) as db:
        approved = {lang: (jobs.get_result(db, record, lang, config) or {"approved": None})["approved"]
                    for lang in ("de", "fr")}
    missing = [lang.upper() for lang, text in approved.items() if not text]
    if missing:
        return jsonify(message=f"Approve the {' and '.join(missing)} summary before exporting."), 409
    doc = export.document(record, approved["de"], approved["fr"])
    response = send_file(BytesIO(export.RENDER[fmt](doc)), mimetype=export.FORMATS[fmt],
                         as_attachment=fmt != "html", download_name=export.filename(record["docket"], fmt))
    response.headers["Cache-Control"] = "private, no-store"
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


def warm_precedent_index():
    """Build the precedent-search index in the background, so the first search is as fast as the next ones."""
    import case_search

    try:
        with closing(connect()) as db:
            extra = review_store.added_cases(db)
        with closing(kb()) as repo:
            count = case_search.warm(review_kb.window_records(repo, window(), extra, include_text=True))
        app.logger.info("precedent_index_ready judgements=%d", count)
    except (OSError, sqlite3.Error) as exc:
        app.logger.warning("precedent_index_failed error=%s", type(exc).__name__)


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
        with closing(connect()) as db:
            review_store.interrupt_computing(db)  # a calculation cannot survive a restart
        threading.Thread(target=warm_precedent_index, name="precedent-index", daemon=True).start()
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
