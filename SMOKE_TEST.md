# Smoke test

Checks that the pipeline and the review app work end to end, the way a reviewer uses them. Run it before a demo,
after a deployment, or after changing the environment.

- **Automated:** `scripts/smoke.py` builds a knowledge base, starts the real server and drives it over HTTP. It then
  checks the databases after every step and runs the failure checks. It always works in a fresh scratch directory and
  never touches `data/`.
- **Manual:** the same flows step by step in sections 3 and 4, for a production-like install with real data.

## 1. Build and start in production mode

The review app is a local, single-reviewer Flask server. `src/app.py serve` binds to `127.0.0.1` with debug off.
There is no separate WSGI server in the dependencies, so run it on the reviewer's machine and don't expose it to a
network.

### Environment

Copy `.env.example` to `.env` and fill it in. Variables already set in the shell take precedence over `.env`.

| Variable | Needed for | Checked at startup |
|---|---|---|
| `LLM_BASE_URL`, `LLM_NAME` | drafts, briefing scripts, answers (Apertus) | **required**: must be set, HTTPS unless localhost |
| `LLM_API_KEY` | the Apertus gateway | used by `python -m suva llm-ping` |
| `DATABASE_URL` | knowledge base (default `sqlite:///data/kb.sqlite`) | **required**: the file must exist |
| `DATA_DIR` | review DB, caches, scoring output (default `data`) | |
| `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID_DE`, `ELEVENLABS_VOICE_ID_FR` | audio briefings and voice questions | optional: a warning per missing language |
| `LOG_LEVEL` | log verbosity (default `INFO`) | |

### Commands

Run these from the repo root. You need Python 3.11+ and Node.js 20.19+ or 22.12+.

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -e . pytest
npm --prefix src/frontend ci && npm --prefix src/frontend run build      # -> src/frontend/dist/
cp .env.example .env                                                     # then fill in the values
python -m suva llm-ping                                                  # Apertus reachable? prints served_model

# Fill the knowledge base (first fetch downloads ~925 MB from OpenCaseLaw)
python -m suva init-db
python -m suva import-supplied
python -m suva split-examples --manifest data/split-manifest.json
python -m suva fetch --window challenge
python -m suva parse
python -m suva score

# Start, refusing to serve if a required part is missing
.venv/bin/python src/app.py serve --strict          # http://127.0.0.1:5050 ; add --today YYYY-MM-DD to pin the window
```

On startup the server logs its configuration to stderr. It never logs keys:

```
INFO app startup_config kb=…/data/kb.sqlite review_db=…/data/review.sqlite model=swiss-ai/Apertus-v1.5-70B llm_host=… today=today
WARNING app startup_check_failed required=no problem='ElevenLabs fr: set ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID_FR …'
INFO app startup_check_ok optional_missing=1
```

If the LLM configuration, the frontend build or the knowledge base is missing, it logs one
`startup_check_failed required=yes problem=…` line per problem. With `--strict` it then logs `startup_refused` and
exits with code 2. Without `--strict` it serves anyway: the queue works, but drafts fail and show the configuration problem
(e.g. "Configure LLM_BASE_URL and LLM_NAME for Apertus").

## 2. Automated smoke test

```bash
python scripts/smoke.py              # offline, ~5 s: no credentials, Apertus is a closed local port
python scripts/smoke.py --live       # real Apertus and ElevenLabs from .env: drafts, approval, audio, Q&A (minutes)
```

It prints `PASS`/`FAIL`/`SKIP` per check, then `N/N checks passed` and the scratch directory, which holds
`kb.sqlite`, `review.sqlite` and the server logs. It exits with 0 only if every check passed. Offline it skips
approval and audio, because those need a real draft.

`--live` sends one synthetic, clearly fictional judgement (`8C_999/2026`) to Apertus and its briefing to
ElevenLabs. That uses your quota.

## 3. Critical flows: steps and expected state

Use a scratch copy when you try this on a machine with real data:
`export DATA_DIR=/tmp/suva-smoke DATABASE_URL=sqlite:////tmp/suva-smoke/kb.sqlite`. Open http://127.0.0.1:5050
after starting the server as in section 1.

### Flow 1: fill the knowledge base (CLI)

| Step | Check |
|---|---|
| `python -m suva init-db` | prints `applied: 001_…` the first time, `nothing (up to date)` after |
| `python -m suva import-supplied` | JSON with example and pick counts; `sqlite3 $KB "SELECT COUNT(*) FROM example_summaries"` > 0 |
| `python -m suva split-examples --manifest data/split-manifest.json` | the manifest file exists; `SELECT split, COUNT(*) FROM example_summaries GROUP BY split` shows `fewshot` and `eval` |
| `python -m suva fetch --window challenge` | exit 0, stderr ends with `fetch_done … errors=0`; `SELECT COUNT(*) FROM judgements` > 0 |
| `python -m suva parse` | `parse_done … status={'ok': …}`; `SELECT status, COUNT(*) FROM parses GROUP BY status` |
| `python -m suva score` | `"scored": N`, then the top N scores of this window; `SELECT band, COUNT(*) FROM relevance_scores GROUP BY band` |

Here `$KB` is the knowledge-base file, `data/kb.sqlite` by default.

### Flow 2: the queue and adding a case

1. Open the app. The queue lists the window's judgements, highest score first, each with a status and the top
   reason. The header shows the publication window.
2. Select **Add judgment**. Enter a docket that is in the knowledge base (e.g. one from the queue) and submit. Or
   upload a UTF-8 `.txt` file with a docket and decision date.
3. Check: the case opens with "Judgment added.". The upload shows a status, score and reasons.
   - `sqlite3 data/review.sqlite "SELECT case_key FROM review_cases"` contains the key.
   - For an upload: `sqlite3 $KB "SELECT text_source FROM judgements WHERE key='<key>'"` is `manual`, and
     `SELECT status FROM parses WHERE key='<key>'` is `ok`.

### Flow 3: draft the DE and FR summaries

1. Open a case marked Relevant or Review, go to **Legal summaries** and select **Summarize judgment**.
2. Check: "Generating summary…" appears, then both drafts with supporting passages. The server log shows
   `summary_ready case=<key> kind=facts`, then `kind=de` and `kind=fr`.
   `SELECT kind FROM results WHERE case_key='<key>' AND payload IS NOT NULL` in `data/review.sqlite` returns
   `facts`, `de`, `fr`.

### Flow 4: review and approve

1. Edit the German draft and select **Approve**.
2. Check: "Summary approved." and the label changes to "Approved summary". The log shows
   `summary_approved case=<key> lang=de`. `SELECT approved FROM results WHERE case_key='<key>' AND kind='de'` holds
   the edited text. Reloading the page keeps it.

### Flow 5: audio briefing and questions

1. With a DE summary approved and ElevenLabs configured, select **Generate briefing**.
2. Check: the player appears with the script and source excerpts. The log shows
   `briefing_ready case=<key> lang=de … bytes=N`. `SELECT length(mp3) FROM briefings` is greater than 0.
3. Open **Ask this judgment**, start a case call and ask "Wie hat das Bundesgericht entschieden?".
4. Check: the answer is spoken and cites original lines. Nothing is stored server-side, and the log contains no
   question text.

## 4. Deliberate failure checks

| # | Action | Expected clean behaviour |
|---|---|---|
| 1 | **Invalid input:** add docket `123AB_5/2025`; or a decision date `2026-13-01`; or upload a non-UTF-8 file | HTTP 400 and the message "Enter a Swiss case ID such as 8C_123/2026." / "Enter dates as YYYY-MM-DD." / "Could not add that judgment. Upload a UTF-8 text file.". `SELECT COUNT(*) FROM judgements` is unchanged. The log shows `add_case_rejected`. |
| 2 | **Apertus unreachable:** start with `LLM_BASE_URL=http://127.0.0.1:9/v1 LLM_MAX_RETRIES=0`, then summarize a case and ask a question | The drafts end with an error instead of spinning, and the factual-brief row stores "Apertus request failed. Try again.". The question returns 502 "Apertus could not complete this request. Try again.". The log shows `summary_failed … error=OSError` and `conversation_provider_failed action=ask case=<key> provider=Apertus`, with no question text. The queue keeps working. |
| 3 | **Duplicate or stale action:** add the same docket twice; upload a different text for an existing decision; approve with an old draft version (two tabs) | The second add succeeds and `review_cases` still has one row. The different text gives 400 "This judgment already has source text. Add it from the knowledge base." and the original is kept. The stale approval gives 409 "The draft changed. Reload it and review it before approving." and nothing is approved. |
| 4 | **Missing configuration:** `LLM_BASE_URL= .venv/bin/python src/app.py serve --strict` | Exit code 2 with `startup_check_failed required=yes problem='LLM: Configure LLM_BASE_URL and LLM_NAME for Apertus …'` and `startup_refused problems=1`. The server never starts. |

`scripts/smoke.py` runs all four checks automatically.

## Troubleshooting

- **`GET /` returns 503:** build the frontend (section 1).
- **Every row in the queue shows "Stale":** the rules, the parse or the text changed. Run `python -m suva score`.
- **Drafts show "Configure LLM_BASE_URL and LLM_NAME…" or "Use HTTPS…":** fix the `.env` values, then run
  `python -m suva llm-ping`.
- **More detail:** set `LOG_LEVEL=DEBUG` to include cache misses and the handling of unparsed Retry-After values.
