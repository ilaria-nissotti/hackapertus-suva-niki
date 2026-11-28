#!/usr/bin/env python3
"""End-to-end smoke test of the pipeline CLI and the review app, as a reviewer would use them.

    python scripts/smoke.py            # offline: no credentials; Apertus is a closed local port
    python scripts/smoke.py --live     # real Apertus (and ElevenLabs if configured) from .env / env

Everything runs in a fresh scratch directory (KB, review DB, split manifest), never in data/.
The app is started for real (`src/app.py serve --strict`) and driven over HTTP. Exit code 0 means
every check passed; the server log is kept for inspection. See SMOKE_TEST.md.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKET = "8C_999/2026"  # fictional docket for a synthetic judgement; only ever stored in the scratch KB


def judgement_text(decided: date) -> str:
    """A synthetic, clearly fictional accident-insurance judgement shaped like a real BGer text."""
    month = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
             "November", "Dezember"][decided.month - 1]
    paragraphs = [
        "Bundesgericht\nTribunal fédéral", DOCKET, f"Urteil vom {decided.day}. {month} {decided.year}",
        "IV. öffentlich-rechtliche Abteilung", "Besetzung\nBundesrichter Muster, Präsident,\nGerichtsschreiberin Beispiel.",
        "Verfahrensbeteiligte", "A.________, (Smoke-Test, fiktive Person),\nBeschwerdeführer,", "gegen",
        "Suva, Fluhmattstrasse 1, 6002 Luzern,\nBeschwerdegegnerin.", "Gegenstand",
        "Unfallversicherung (Kausalzusammenhang; Taggeld),",
        f"Beschwerde gegen das Urteil des Versicherungsgerichts des Kantons Muster vom 1. Juli {decided.year}.",
        "Sachverhalt:", "A.",
        "Der Versicherte stürzte am Arbeitsplatz von einer Leiter und zog sich eine Schulterverletzung zu. "
        "Die Suva erbrachte zunächst Taggeld und Heilbehandlung.", "B.",
        "Mit Verfügung stellte die Suva ihre Leistungen ein, weil die weiterhin geklagten Beschwerden nicht mehr "
        "in einem natürlichen Kausalzusammenhang mit dem Unfall stünden.", "C.",
        "Das kantonale Gericht wies die dagegen erhobene Beschwerde ab. Der Versicherte führt Beschwerde in "
        "öffentlich-rechtlichen Angelegenheiten.",
        "Erwägungen:", "1.",
        "Die Beschwerde in öffentlich-rechtlichen Angelegenheiten ist zulässig; das Bundesgericht prüft die "
        "Anwendung von Bundesrecht frei.", "2.",
        "Nach Art. 6 Abs. 1 UVG werden die Versicherungsleistungen bei Berufsunfällen gewährt, soweit das Gesetz "
        "nichts anderes bestimmt. Die Leistungspflicht setzt einen natürlichen und adäquaten Kausalzusammenhang voraus.",
        "3.",
        "Die Vorinstanz hat gestützt auf die beweiskräftigen ärztlichen Berichte willkürfrei festgestellt, dass der "
        "Status quo sine spätestens sechs Monate nach dem Unfall erreicht war. Die Einwände des Beschwerdeführers "
        "vermögen daran nichts zu ändern.", "4.",
        "Die Beschwerde ist demnach abzuweisen. Die Gerichtskosten trägt der unterliegende Beschwerdeführer "
        "(Art. 66 Abs. 1 BGG).",
        "Demnach erkennt das Bundesgericht:", "1.", "Die Beschwerde wird abgewiesen.", "2.",
        "Die Gerichtskosten von Fr. 800.- werden dem Beschwerdeführer auferlegt.", "3.",
        "Dieses Urteil wird den Parteien schriftlich mitgeteilt.",
    ]
    return "\n\n".join(paragraphs) + "\n"


class Smoke:
    def __init__(self, live: bool, today: date):
        self.live, self.today = live, today
        self.work = Path(tempfile.mkdtemp(prefix="suva-smoke-"))
        self.results: list[tuple[str, bool, str]] = []
        self.servers: list[subprocess.Popen] = []
        self.base = ""
        self.secret = f"sk-smoke-{uuid.uuid4().hex}"  # offline dummy key: must never show up in a log

    # --- reporting --------------------------------------------------------------------------
    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.results.append((name, bool(ok), detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""), flush=True)
        return bool(ok)

    def section(self, title: str) -> None:
        print(f"\n== {title}", flush=True)

    # --- environment ------------------------------------------------------------------------
    def env(self, **overrides: str) -> dict[str, str]:
        env = dict(os.environ)
        env.update(DATA_DIR=str(self.work), DATABASE_URL=f"sqlite:///{self.work / 'kb.sqlite'}", LOG_LEVEL="INFO",
                   PYTHONUNBUFFERED="1")
        if not self.live:  # a local port with nothing listening: every Apertus call fails fast
            env.update(LLM_NAME="smoke-model", LLM_BASE_URL=f"http://127.0.0.1:{free_port()}/v1", LLM_API_KEY=self.secret,
                       LLM_TIMEOUT="3", LLM_MAX_RETRIES="0", ELEVENLABS_API_KEY="", ELEVENLABS_VOICE_ID_DE="",
                       ELEVENLABS_VOICE_ID_FR="")
        env.update(overrides)
        return env

    def suva(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "suva", *args], cwd=ROOT, env=env or self.env(),
                              capture_output=True, text=True, timeout=600)

    def db(self, name: str) -> sqlite3.Connection:
        conn = sqlite3.connect(self.work / name)
        conn.row_factory = sqlite3.Row
        return conn

    # --- server -----------------------------------------------------------------------------
    def start_server(self, env: dict, log_name: str) -> tuple[subprocess.Popen, str, Path]:
        port = free_port()
        log_path = self.work / log_name
        proc = subprocess.Popen(
            [sys.executable, "src/app.py", "--today", self.today.isoformat(), "serve", "--strict", "--port", str(port)],
            cwd=ROOT, env=env, stdout=log_path.open("w"), stderr=subprocess.STDOUT)
        self.servers.append(proc)
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            if proc.poll() is not None:
                break
            try:
                urllib.request.urlopen(base + "/api/dashboard", timeout=1).close()
                return proc, base, log_path
            except OSError:
                time.sleep(0.2)
        raise RuntimeError(f"server did not start (exit {proc.poll()}); see {log_path}")

    def request(self, method: str, path: str, *, form: dict | None = None, files: dict | None = None,
                json_body: dict | None = None, base: str | None = None) -> tuple[int, dict | bytes, str]:
        headers = {"Accept": "application/json"}
        data = None
        if json_body is not None:
            data, headers["Content-Type"] = json.dumps(json_body).encode(), "application/json"
        elif files:
            boundary = uuid.uuid4().hex
            parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                     for k, v in (form or {}).items()]
            for k, (filename, content) in files.items():
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{filename}"\r\n'
                             f"Content-Type: text/plain\r\n\r\n".encode() + content + b"\r\n")
            data = b"".join(parts) + f"--{boundary}--\r\n".encode()
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        elif form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request((base or self.base) + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                status, body, ctype = resp.status, resp.read(), resp.headers.get_content_type()
        except urllib.error.HTTPError as exc:
            status, body, ctype = exc.code, exc.read(), exc.headers.get_content_type()
        return status, (json.loads(body) if ctype == "application/json" and body else body), ctype

    def dashboard(self, **query: str) -> dict:
        return self.request("GET", "/api/dashboard?" + urllib.parse.urlencode(query))[1]

    def wait_for_drafts(self, key: str, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            selected = self.dashboard(case=key)["selected"]
            done = all(not selected[l]["active"] and (selected[l]["version"] or selected[l]["error"]) for l in ("de", "fr"))
            if done or time.monotonic() > deadline:
                return selected
            time.sleep(1)

    # --- the run ----------------------------------------------------------------------------
    def run(self) -> int:
        print(f"Smoke test ({'live' if self.live else 'offline'}), scratch dir {self.work}, today {self.today}")
        try:
            self.preflight()
            self.pipeline()
            self.serve()
            key = self.flow_add_judgement()
            self.flow_cli_on_app_data(key)
            draft = self.flow_summaries(key)
            self.flow_approve(key, draft)
            self.flow_briefing(key)
            self.flow_conversation(key)
            self.failure_invalid_input()
            self.failure_disconnected_dependency(key)
            self.failure_duplicate_actions(key)
            self.failure_startup_without_config()
        except Exception as exc:  # noqa: BLE001 - a crash in the smoke run is itself a failed check
            self.check("smoke run completed", False, f"{type(exc).__name__}: {exc}")
        finally:
            for proc in self.servers:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
        failed = [name for name, ok, _ in self.results if not ok]
        print(f"\n{len(self.results) - len(failed)}/{len(self.results)} checks passed. Server logs and data: {self.work}")
        if failed:
            print("Failed: " + "; ".join(failed))
        return 1 if failed else 0

    def preflight(self) -> None:
        self.section("Preflight")
        self.check("Python 3.11+", sys.version_info >= (3, 11), sys.version.split()[0])
        self.check("frontend is built (src/frontend/dist/index.html)", (ROOT / "src/frontend/dist/index.html").is_file(),
                   "run: npm --prefix src/frontend ci && npm --prefix src/frontend run build")
        self.check("supplied examples present (judgements/)", any((ROOT / "judgements").glob("Export_*.html")))
        if self.live:
            # Its own database: llm-ping migrates the KB it opens, which would leave init-db nothing to apply.
            ping = self.suva("llm-ping", env=self.env(DATABASE_URL=f"sqlite:///{self.work / 'llm-ping.sqlite'}"))
            self.check("Apertus reachable (suva llm-ping)", ping.returncode == 0, ping.stderr.strip()[-200:])

    def pipeline(self) -> None:
        self.section("Flow 1: build the knowledge base (suva CLI)")
        r = self.suva("init-db")
        self.check("init-db applies the migrations", r.returncode == 0 and "applied: 001" in r.stdout, r.stderr[-300:])
        r = self.suva("import-supplied")
        with self.db("kb.sqlite") as kb:
            examples = kb.execute("SELECT COUNT(*) FROM example_summaries").fetchone()[0]
            picks = kb.execute("SELECT COUNT(*) FROM known_positives").fetchone()[0]
        self.check("import-supplied stores Suva's examples and picks", r.returncode == 0 and examples > 0 and picks > 0,
                   f"exit {r.returncode}, {examples} examples, {picks} picks")
        manifest = self.work / "split-manifest.json"
        r = self.suva("split-examples", "--manifest", str(manifest))
        with self.db("kb.sqlite") as kb:
            fewshot = kb.execute("SELECT COUNT(*) FROM example_summaries WHERE split='fewshot'").fetchone()[0]
            evaluation = kb.execute("SELECT COUNT(*) FROM example_summaries WHERE split='eval'").fetchone()[0]
        self.check("split-examples freezes a few-shot/eval split and writes the manifest",
                   r.returncode == 0 and manifest.is_file() and fewshot > 0 and evaluation > 0,
                   f"exit {r.returncode}, fewshot {fewshot}, eval {evaluation}")
        again = self.suva("split-examples", "--manifest", str(manifest))
        self.check("replaying the manifest keeps the split", again.returncode == 0, again.stderr[-300:])

    def serve(self) -> None:
        self.section("Start the review app (src/app.py serve --strict)")
        _, self.base, self.log = self.start_server(self.env(), "server.log")
        log = self.log.read_text()
        self.check("startup check passes and logs the configuration",
                   "startup_check_ok" in log and "startup_config" in log, log[-400:])
        secrets = [self.secret] if not self.live else [v for v in secret_values() if v]
        self.check("startup log contains no API key", not any(s in log for s in secrets))
        status, body, ctype = self.request("GET", "/")
        self.check("GET / serves the built frontend", status == 200 and ctype == "text/html", f"{status} {ctype}")
        data = self.dashboard()
        self.check("empty queue loads", data["count"] == 0 and data["rows"] == [], json.dumps(data)[:200])

    def flow_add_judgement(self) -> str:
        self.section("Flow 2: add a judgement by upload; it is parsed and scored")
        decided = self.today - timedelta(days=30)
        status, body, _ = self.request("POST", "/add", form={"docket": DOCKET, "decision_date": decided.isoformat()},
                                       files={"text": ("judgement.txt", judgement_text(decided).encode())})
        key = f"{DOCKET}@{decided.isoformat()}"
        self.check("POST /add returns the new case key", status == 200 and body.get("case") == key, str(body))
        row = next((r for r in self.dashboard()["rows"] if r["key"] == key), None)
        self.check("the case is in the queue with a score and a reason",
                   bool(row and row["score"] is not None and row["issue"]), str(row))
        self.check("the case is flagged for review", bool(row and row["status"] in ("Relevant", "Review")), str(row))
        with self.db("kb.sqlite") as kb:
            j = kb.execute("SELECT text_source FROM judgements WHERE key=?", (key,)).fetchone()
            p = kb.execute("SELECT status, passages FROM parses WHERE key=?", (key,)).fetchone()
            s = kb.execute("SELECT band FROM relevance_scores WHERE key=?", (key,)).fetchone()
        self.check("KB: judgement stored from the manual upload", bool(j and j["text_source"] == "manual"), str(j and dict(j)))
        self.check("KB: parsed into passages", bool(p and p["status"] == "ok" and p["passages"] > 3), str(p and dict(p)))
        self.check("KB: rules score stored", s is not None)
        with self.db("review.sqlite") as review:
            added = review.execute("SELECT COUNT(*) FROM review_cases WHERE case_key=?", (key,)).fetchone()[0]
        self.check("review DB: case recorded as hand-added", added == 1)
        return key

    def flow_cli_on_app_data(self, key: str) -> None:
        self.section("Flow 3: pipeline commands on the same KB")
        r = self.suva("parse")
        parsed = json.loads(r.stdout) if r.returncode == 0 else {}
        self.check("parse reuses the app's current parse", parsed.get("skipped_unchanged") == 1, r.stderr[-300:])
        self.check("parse logs a structured summary", "parse_done judgements=1" in r.stderr, r.stderr[-300:])
        r = self.suva("score", "--window", "challenge", "--today", self.today.isoformat())
        self.check("score scores the challenge window, listing the case", r.returncode == 0 and '"scored": 1' in r.stdout
                   and key in r.stdout, (r.stdout + r.stderr)[-300:])
        self.check("the stored score is still current in the queue",
                   self.dashboard(case=key)["selected"]["score_current"])

    def flow_summaries(self, key: str) -> dict:
        self.section("Flow 4: draft DE/FR summaries (Apertus)")
        status, _, _ = self.request("POST", "/run", form={"case": key, "action": "summarize"})
        self.check("POST /run queues the drafts", status == 200)
        selected = self.wait_for_drafts(key, timeout=900 if self.live else 60)
        if not self.live:
            self.check("offline: drafts end with a visible error, not a hang",
                       all(selected[l]["error"] and not selected[l]["active"] for l in ("de", "fr")),
                       json.dumps({l: selected[l] for l in ("de", "fr")})[:300])
            return {}
        ok = self.check("both drafts are ready", all(selected[l]["version"] for l in ("de", "fr")),
                        json.dumps({l: selected[l]["error"] for l in ("de", "fr")}))
        if ok:
            self.check("each draft has a summary and verified quotes",
                       all(selected[l]["data"].get("summary") and selected[l]["data"].get("quotes") for l in ("de", "fr")))
            with self.db("review.sqlite") as review:
                kinds = {r[0] for r in review.execute(
                    "SELECT kind FROM results WHERE case_key=? AND payload IS NOT NULL", (key,))}
            self.check("review DB: factual brief and both drafts stored", kinds == {"facts", "de", "fr"}, str(kinds))
        return selected

    def flow_approve(self, key: str, selected: dict) -> None:
        self.section("Flow 5: review and approve")
        if not self.live:
            print("  SKIP  approval needs a draft (run with --live)")
            return
        draft = selected["de"]
        text = draft["data"]["summary"].strip() + "\n\n[Geprüft im Smoke-Test]"
        status, body, _ = self.request("POST", "/approve", form={"case": key, "lang": "de", "summary": text,
                                                                 "version": draft["version"]})
        self.check("POST /approve accepts the current version", status == 200, str(body))
        with self.db("review.sqlite") as review:
            approved = review.execute("SELECT approved FROM results WHERE case_key=? AND kind='de' AND version=?",
                                      (key, draft["version"])).fetchone()
        self.check("review DB: the edited text is stored as approved", bool(approved and approved[0] == text.strip()))
        self.check("the queue shows the approved text", self.dashboard(case=key)["selected"]["de"]["approved"] == text.strip())

    def flow_briefing(self, key: str) -> None:
        self.section("Flow 6: audio briefing (ElevenLabs)")
        audio = self.dashboard(case=key, lang="de")["audio"]
        if not self.live or not audio.get("configured"):
            print("  SKIP  needs --live, an approved DE summary and ELEVENLABS_API_KEY + ELEVENLABS_VOICE_ID_DE")
            return
        status, body, _ = self.request("POST", "/briefing", form={"case": key, "lang": "de"})
        self.check("POST /briefing queues the narration", status == 202, str(body))
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            audio = self.dashboard(case=key, lang="de")["audio"]
            if not audio.get("active") and (audio.get("ready") or audio.get("error")):
                break
            time.sleep(2)
        self.check("briefing is ready with a script and source excerpts",
                   audio.get("ready") and audio.get("script") and audio.get("excerpts"), str(audio.get("error")))
        if audio.get("ready"):
            status, mp3, ctype = self.request("GET", f"/briefing/{audio['key']}.mp3?" +
                                              urllib.parse.urlencode({"case": key, "lang": "de"}))
            self.check("the MP3 is served from the review DB", status == 200 and ctype == "audio/mpeg" and len(mp3) > 1000,
                       f"{status} {ctype}")

    def flow_conversation(self, key: str) -> None:
        self.section("Flow 7: ask the judgement a question")
        status, body, _ = self.request("POST", "/conversation/ask", json_body={
            "case": key, "lang": "de", "question": "Wie hat das Bundesgericht entschieden und weshalb?"})
        if not self.live:
            self.check("offline: Apertus down gives a clean 502", status == 502 and body.get("error") ==
                       "Apertus could not complete this request. Try again.", f"{status} {body}")
            return
        self.check("the answer is classified and grounded", status == 200 and body.get("kind") in
                   ("court", "score", "uncertain") and (body["kind"] != "court" or body.get("citations")), str(body)[:300])

    # --- deliberate failures -------------------------------------------------------------------
    def failure_invalid_input(self) -> None:
        self.section("Failure 1: invalid input is refused and nothing is stored")
        with self.db("kb.sqlite") as kb:
            before = kb.execute("SELECT COUNT(*) FROM judgements").fetchone()[0]
        status, body, _ = self.request("POST", "/add", form={"docket": "123AB_5/2025", "decision_date": "2026-10-01"},
                                       files={"text": ("x.txt", judgement_text(self.today).encode())})
        self.check("malformed docket -> 400 'Enter a Swiss case ID such as 8C_123/2026.'",
                   status == 400 and body.get("message") == "Enter a Swiss case ID such as 8C_123/2026.", f"{status} {body}")
        status, body, _ = self.request("POST", "/add", form={"docket": "8C_998/2026", "decision_date": "2026-13-01"},
                                       files={"text": ("x.txt", judgement_text(self.today).encode())})
        self.check("invalid date -> 400 'Enter dates as YYYY-MM-DD.'",
                   status == 400 and body.get("message") == "Enter dates as YYYY-MM-DD.", f"{status} {body}")
        status, body, _ = self.request("POST", "/add", form={"docket": "8C_997/2026", "decision_date": "2026-10-01"},
                                       files={"text": ("x.txt", b"\xff\xfe binary")})
        self.check("non-UTF-8 upload -> 400 'Could not add that judgment. Upload a UTF-8 text file.'",
                   status == 400 and body.get("message") == "Could not add that judgment. Upload a UTF-8 text file.",
                   f"{status} {body}")
        with self.db("kb.sqlite") as kb:
            after = kb.execute("SELECT COUNT(*) FROM judgements").fetchone()[0]
        self.check("KB unchanged after the refused uploads", after == before, f"{before} -> {after}")

    def failure_disconnected_dependency(self, key: str) -> None:
        self.section("Failure 2: Apertus unreachable")
        base, log = self.base, self.log
        if self.live:  # a second server on the same data, pointed at a closed port
            _, base, log = self.start_server(self.env(LLM_BASE_URL=f"http://127.0.0.1:{free_port()}/v1",
                                                      LLM_MAX_RETRIES="0", LLM_TIMEOUT="3"), "server-dead-llm.log")
        status, body, _ = self.request("POST", "/conversation/ask", base=base, json_body={
            "case": key, "lang": "fr", "question": "Quelle est la décision ?"})
        self.check("question -> 502 'Apertus could not complete this request. Try again.'", status == 502 and
                   body.get("error") == "Apertus could not complete this request. Try again.", f"{status} {body}")
        with self.db("review.sqlite") as review:
            facts = review.execute("SELECT error FROM results WHERE case_key=? AND kind='facts' AND error IS NOT NULL",
                                   (key,)).fetchone()
        if not self.live:
            self.check("summary job stored 'Apertus request failed. Try again.' for the factual brief",
                       bool(facts and facts[0] == "Apertus request failed. Try again."), str(facts and facts[0]))
        time.sleep(0.5)
        text = log.read_text()
        self.check("the server log names the failing step and case, without the question",
                   "conversation_provider_failed action=ask" in text and key in text
                   and "Quelle est la décision" not in text)
        self.check("the app keeps serving", self.request("GET", "/api/dashboard", base=base)[0] == 200)

    def failure_duplicate_actions(self, key: str) -> None:
        self.section("Failure 3: duplicate and stale actions")
        for _ in range(2):
            status, body, _ = self.request("POST", "/add", form={"docket": DOCKET})
        self.check("adding the same case twice succeeds", status == 200 and body.get("case") == key, str(body))
        with self.db("review.sqlite") as review:
            count = review.execute("SELECT COUNT(*) FROM review_cases WHERE case_key=?", (key,)).fetchone()[0]
        self.check("review DB: still one hand-added row", count == 1, str(count))
        decided = key.partition("@")[2]
        status, body, _ = self.request("POST", "/add", form={"docket": DOCKET, "decision_date": decided},
                                       files={"text": ("other.txt", (judgement_text(self.today) + "\nAnders.").encode())})
        self.check("a different text for the same decision -> 400, original kept",
                   status == 400 and "already has source text" in body.get("message", ""), f"{status} {body}")
        status, body, _ = self.request("POST", "/approve", form={"case": key, "lang": "de", "summary": "Veraltet.",
                                                                 "version": "stale-version"})
        self.check("approving a stale draft version -> 409 'The draft changed. Reload it ...'",
                   status == 409 and body.get("message", "").startswith("The draft changed."), f"{status} {body}")
        with self.db("review.sqlite") as review:
            stale = review.execute("SELECT COUNT(*) FROM results WHERE approved='Veraltet.'").fetchone()[0]
        self.check("review DB: nothing approved by the stale request", stale == 0)

    def failure_startup_without_config(self) -> None:
        self.section("Failure 4: start without the LLM configuration")
        r = subprocess.run([sys.executable, "src/app.py", "serve", "--strict", "--port", str(free_port())], cwd=ROOT,
                           env=self.env(LLM_BASE_URL=""), capture_output=True, text=True, timeout=60)
        self.check("serve --strict exits 2 and names the missing setting", r.returncode == 2 and "startup_refused" in r.stderr
                   and "Configure LLM_BASE_URL and LLM_NAME" in r.stderr, f"exit {r.returncode}: {r.stderr[-300:]}")


def secret_values() -> list[str]:
    """The configured API keys (environment first, then .env), to prove they never reach a log."""
    names = ("LLM_API_KEY", "ELEVENLABS_API_KEY")
    values = {n: os.environ.get(n, "") for n in names}
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() in names and not values[name.strip()]:
                values[name.strip()] = value.strip().strip("'\"")
    return [v for v in values.values() if len(v) >= 8]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="use the real Apertus/ElevenLabs settings from .env")
    parser.add_argument("--today", type=date.fromisoformat, default=date.today(), help="challenge window reference date")
    args = parser.parse_args()
    return Smoke(args.live, args.today).run()


if __name__ == "__main__":
    sys.exit(main())
