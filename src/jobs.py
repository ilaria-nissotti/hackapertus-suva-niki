"""Background work of the review app: the summary pipeline, the job queue and briefing jobs.

No Flask here. `settings` holds DATABASE, KB and TODAY; `app.py` binds it to Flask's `app.config`, so the web
server, the batch commands and the tests all read the same values.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import fcntl
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import sys
import threading

import briefing
import review_kb
import review_store
from bger import model_config
from summaries import VERSION, draft, make_facts

log = logging.getLogger("app")  # the Flask app's logger, so log lines read the same as before the split
settings = {}
JOB_CRASHED = "The summary job failed unexpectedly. Try again."

# Summary and briefing jobs run side by side, like summarize-top; summary_lock stops duplicate Apertus calls.
pool = ThreadPoolExecutor(max_workers=4)
pending = set()
pending_lock = threading.Lock()


def connect():
    return review_store.connect(settings["DATABASE"])


def kb():
    return review_kb.open_kb(settings["KB"])


def window():
    return review_kb.window_for(settings["TODAY"])


def load_record(key):
    if not isinstance(key, str):
        return None
    with closing(kb()) as repo:
        return review_kb.get_record(repo, key)


def current_config():
    try:
        return model_config()
    except ValueError as exc:
        return {"LLM_NAME": "unconfigured", "error": str(exc)}


def version(record, model, endpoint=""):
    digest = hashlib.sha256(record["full_text"].encode()).hexdigest()
    return f"{VERSION}:{model}:{hashlib.sha256(endpoint.encode()).hexdigest()}:{digest}"


def generation_version(record, config):
    return version(record, config["LLM_NAME"], config.get("LLM_BASE_URL", ""))


def get_result(db, record, kind, config):
    return review_store.get_result(db, record["key"], kind, generation_version(record, config))


@contextmanager
def summary_lock(record, kind, config):
    """One generation per draft at a time, also across the web server and `summarize-top`."""
    lock_dir = Path(settings["DATABASE"]).parent / "summary-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(f"{record['key']}\0{kind}\0{generation_version(record, config)}".encode()).hexdigest()
    with (lock_dir / name).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def process(key, kind, *, record=None, config=None):
    record = load_record(key) if record is None else record
    if record is None:
        return False
    config = current_config() if config is None else config
    with summary_lock(record, kind, config), closing(connect()) as db:
        model = config["LLM_NAME"]
        existing = get_result(db, record, kind, config)
        if existing and existing["payload"]:
            return True
        payload = None
        error = None
        try:
            if not record["full_text"].strip():
                raise ValueError("The judgment has no usable source text.")
            if model == "unconfigured":
                raise ValueError("Missing model configuration")
            if kind == "facts":
                payload = make_facts(config, record["full_text"])
            else:
                if not process(key, "facts", record=record, config=config):
                    raise ValueError("The factual brief failed. Retry summary generation.")
                facts = json.loads(get_result(db, record, "facts", config)["payload"])
                with closing(kb()) as repo:
                    style_examples = repo.list_examples(split="fewshot")
                payload = draft(config, record, facts, kind, style_examples)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            # Error responses and exception arguments can contain provider/request data.
            error = ("The judgment has no usable source text." if not record["full_text"].strip() else
                     f"Provider HTTP {exc.code}" if hasattr(exc, "code") else
                     config.get("error", "Missing model configuration") if model == "unconfigured" else
                     "Apertus request failed. Try again." if isinstance(exc, OSError) else
                     "Could not generate a draft with verified source evidence. Try again.")
            log.warning("summary_failed case=%s kind=%s error=%s reason=%r", key, kind, type(exc).__name__, error)
        review_store.save_result(db, key, kind, generation_version(record, config),
                                 json.dumps(payload, ensure_ascii=False) if payload else None, error)
        if payload is not None:
            log.info("summary_ready case=%s kind=%s model=%s", key, kind, model)
        return payload is not None


def run_summary_job(key, kind):
    try:
        return process(key, kind)
    except Exception as exc:
        log.error("summary_job_crashed case=%s kind=%s error=%s", key, kind, type(exc).__name__)
        try:  # record the failure where the dashboard looks, so the browser shows it instead of nothing
            record = load_record(key)
            if record is not None:
                with closing(connect()) as db:
                    review_store.save_result_error(db, key, kind, generation_version(record, current_config()),
                                                   JOB_CRASHED)
        except Exception as store_exc:  # noqa: BLE001 - the original failure is already logged
            log.error("summary_error_not_stored case=%s kind=%s error=%s", key, kind, type(store_exc).__name__)
        return False


def is_pending(task):
    """Read this before the result row: a job commits its row before clearing the flag."""
    with pending_lock:
        return task in pending


def enqueue(key, kinds):
    with pending_lock:
        tasks = [kind for kind in kinds if (key, kind) not in pending]
        pending.update((key, kind) for kind in tasks)

    def run():
        for kind in tasks:
            try:
                run_summary_job(key, kind)
            finally:
                with pending_lock:
                    pending.discard((key, kind))
    if tasks:
        pool.submit(run)


def enqueue_briefing(key, lang, cache_key, inputs):
    """Queue one briefing per cache key; a second request joins the pending job."""
    task = ("briefing", cache_key)

    def run():
        try:
            briefing.generate(*inputs)
        except Exception as exc:  # a pool thread would swallow it: log it and show an error in the UI
            log.error("briefing_job_crashed case=%s lang=%s cache_key=%s error=%s", key, lang, cache_key[:12],
                      type(exc).__name__)
            try:
                with closing(connect()) as db:
                    review_store.save_briefing_error(db, cache_key, "Briefing failed unexpectedly. Try again.")
            except sqlite3.Error as db_exc:
                log.error("briefing_error_not_stored cache_key=%s error=%s", cache_key[:12], type(db_exc).__name__)
        finally:
            with pending_lock:
                pending.discard(task)

    with pending_lock:
        if task not in pending:
            pending.add(task)
            pool.submit(run)


def summarize_top(limit, workers):
    """Draft DE and FR summaries for the best-scored judgements of the window."""
    with closing(kb()) as repo:
        records = review_kb.window_records(repo, window())
    ranked = sorted((r for r in records if r["status"] in ("Relevant", "Review")),
                    key=lambda r: (-r["score"], -(r["points"] or 0)))[:limit]  # same order as the dashboard
    stale = sum(r["status"] == "Stale" for r in records)
    if stale:
        print(f"note: {stale} judgements have stale scores: run `python -m suva score` first", file=sys.stderr)

    def summarize(row):
        return sum(not run_summary_job(row["key"], lang) for lang in ("de", "fr"))

    failed = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for index, errors in enumerate(executor.map(summarize, ranked), 1):
            failed += errors
            print(f"{index}/{len(ranked)} cases; {failed} tasks need retry", flush=True)
    print(f"{len(ranked)} cases processed; {failed} tasks need retry.")
    return int(bool(failed))
