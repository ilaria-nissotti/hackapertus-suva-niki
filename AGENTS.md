# Suva judgement review

This repository collects Swiss Federal Supreme Court judgements, reconciles them with Suva’s supplied examples, and turns judgement text into passages and citations. It scores each judgement for relevance (0–100) with expert-editable rules and explains each score with the rules that fired. A local review app shows the ranked queue, drafts German and French summaries in Suva’s style with Apertus, and records reviewer approval. Approved summaries can be narrated, and reviewers can ask questions about a judgement (ElevenLabs).

## Code map

| Path | Responsibility |
| --- | --- |
| `src/suva/__main__.py`, `cli.py` | Entry points for `python -m suva` and the installed `suva` command. Keep orchestration in the feature modules. |
| `src/suva/config.py`, `hashing.py` | Environment settings and stable hashes for cache requests, source text, and manifests. |
| `src/suva/ingest/` | `exports.py` reads the bilingual HTML summaries. `picks.py` reads the spreadsheet labels. `importer.py` reconciles the two and replaces the supplied dataset atomically. `split.py` creates or replays the versioned few-shot/evaluation manifest. |
| `src/suva/fetch.py`, `windows.py` | Collect source sightings, match Suva picks, load missing text, and record the result. The dev window uses decision dates. The challenge window uses known publication dates and tracks unknown dates separately. |
| `src/suva/sources/`, `src/suva/http.py` | `opencaselaw.py` reads the Parquet snapshot. `entscheidsuche.py` lists decisions and loads validated HTML attachments. `base.py` defines source records and decision identity. `quality.py` rejects source error pages. `http.py` handles request pacing and retries for entscheidsuche. |
| `src/suva/coverage.py` | Reports the latest fetch run by decision week. Separates queried empty weeks, unqueried ranges, and listings without usable text. |
| `src/suva/parse/` | `segment.py` produces verbatim passage spans. `citations.py` extracts statutes, leading decisions, and docket references. `run.py` reuses current parses and reports failures. |
| `src/suva/storage/` | `base.py` defines the repository interface. `sqlite.py` owns SQL and transactions. `migrations.py` contains the ordered schema changes. |
| `src/suva/scoring/`, `rules/rules.yaml` | `dataset.py` labels judgements in the dev window from the spreadsheet picks and splits them by time. `rules.py` loads and validates `rules.yaml`. `score.py` turns fired rules into a score, band, and explanation without an LLM. `analyze.py`, `evaluate.py`, and `fit.py` propose rules, measure ranking quality, and suggest weights. `store.py` stores the score per judgement for the app (`suva score`). `judge.py` asks Apertus to rate candidates for evaluation. See `src/suva/scoring/README.md`. |
| `src/suva/llm.py` | OpenAI-compatible Apertus calls, endpoint-scoped cache replay, and audit metadata. |
| `src/app.py`, `jobs.py`, `review_store.py`, `review_kb.py` | Flask review app. `app.py` holds the routes and startup checks. `jobs.py` runs the summary pipeline, the job queue and briefing jobs, without Flask. `review_store.py` owns every query on the app’s own `DATA_DIR/review.sqlite` (drafts, approvals, briefings, hand-added cases). `review_kb.py` reads judgements, parses and stored scores from the KB. |
| `src/summaries.py`, `bger.py` | Factual brief and independent DE/FR drafts with few-shot Suva examples. `bger.py` holds config and the Apertus chat call. |
| `src/briefing.py`, `conversation.py` | ElevenLabs narration of approved summaries, and grounded case questions with recap. |
| `src/frontend/` | React/TypeScript workspace built with Vite and served by Flask. |
| `tests/` | Offline tests for the CLI, inputs, adapters, storage, parser, scoring, review app, and Apertus client. HTTP and inference tests use test doubles. `conversation-ui.test.cjs` simulates the browser and media flows. |

## Run and verify

Use Python 3.11 or later. Run these commands from the repo root.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e . pytest
python -m pytest -q
python -m suva init-db
python -m suva import-supplied
python -m suva split-examples --manifest data/split-manifest.json
python -m suva fetch --window dev
python -m suva fetch --window challenge
python -m suva coverage --start 2025-10-01 --end 2026-03-31
python -m suva parse
python -m suva score
python -m suva dataset
python -m suva analyze
python -m suva evaluate
python -m suva fit-weights
```

The first fetch downloads the OpenCaseLaw snapshot and queries entscheidsuche. Use `--no-entscheidsuche` with an existing `data/raw/opencaselaw/bger.parquet` for an offline fetch. `fetch --window challenge --today YYYY-MM-DD` pins the reference date; `score` and `src/app.py` take the same `--today`. For a live gateway check, set `LLM_NAME`, `LLM_BASE_URL`, and `LLM_API_KEY` using `.env.example`, then run `python -m suva llm-ping`. That command bypasses the cache. With the gateway set, `python -m suva judge --limit 30` rates the top candidates and `evaluate --with-judge` adds the judge's points. Scoring outputs go to `data/scoring/`.

## Invariants to preserve

- Use `docket@YYYY-MM-DD` when a decision date is available. Match dated labels exactly and surface ambiguous docket-only matches. Suva’s summary creation date is a separate field.
- Keep publication dates, decision dates, and retrieval timestamps distinct. Coverage describes the sources observed in a run and cannot establish that the court feed is complete.
- Keep source failures visible. Validate HTML and Parquet before replacing cached copies. Stop requests to a source after it refuses access.
- Preserve verbatim passage text and offsets into the stored judgement. Citation offsets are relative to the passage. Changes to source text invalidate derived rows. Bump `PARSER_VERSION` when segmentation or citation output changes.
- Keep SQLite migrations append-only and dataset replacement atomic. Keep frozen split assignments and source hashes reproducible, with decisions from the same docket in the same split.
- Scoring labels follow the same dated matching as fetch and parse. A dataset built from stale parses is reported, not silently used. Tune rules on the train split only. Use `evaluate --split test` for the final number. Bump `judge.PROMPT_VERSION` when the judge prompt changes. The app shows stored scores and flags them as stale when the rules, parse or text changed; run `suva score` again.
- Keep `.env`, local databases (including `review.sqlite`), `.suva-work/`, `src/frontend/dist/`, raw mirror caches, `data/scoring/` outputs, and generated manifests out of commits unless explicitly approved for sharing. Keep audit entries free of prompt text and API keys. Use a scratch database when replaying imports or parsing during review.
- Preserve the organizer’s paths and use [technical_report.md](technical_report.md) for submission run instructions and results.

## Review app

Build the frontend with `npm --prefix src/frontend ci && npm --prefix src/frontend run build` (Node.js 20.19+ or 22.12+). After `fetch`, `parse` and `score`, run `.venv/bin/python src/app.py serve` and open http://127.0.0.1:5050. `src/app.py summarize-top --limit 10` drafts summaries for the best-scored judgements of the window.

- The app does not scrape or score. Judgements come from `suva fetch` and scores from `suva score`. Identify cases by their `docket@date` key; a hand-added docket with several decisions needs its decision date.
- Take style examples only from the few-shot split. Keep the evaluation split unseen by prompts.

- Draft German and French independently from the ruling and a shared factual brief, with examples in the target language. Keep review and approval explicit.
- Audio requires a separate approved summary per language. Apertus writes the script and cites original lines. Send only the script to ElevenLabs. Store and serve audio locally, and invalidate it when the approval, source, or generation settings change.
- Case conversation uses Apertus for answers and recap, with original ruling citations. ElevenLabs handles transcription and narration, separate from approved summaries. Keep recorded questions out of logs and storage.
- Run `node --test tests/conversation-ui.test.cjs` for the simulated browser and media flows. Python tests for the app run under `python -m pytest -q`.
