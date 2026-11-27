# Suva judgement ingestion and relevance scoring

This repository collects Swiss Federal Supreme Court judgements, reconciles them with Suva’s supplied examples, and turns judgement text into passages and citations. It scores each judgement for relevance (0–100) with expert-editable rules and an optional Apertus judge. It has an Apertus client and a gateway diagnostic. Bilingual summary generation and the judge-facing UI are still pending.

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
| `src/suva/scoring/`, `rules/rules.yaml` | `dataset.py` labels judgements in the dev window from the spreadsheet picks and splits them by time. `rules.py` loads and validates `rules.yaml`. `score.py` turns fired rules into a score, band, and explanation without an LLM. `analyze.py`, `evaluate.py`, and `fit.py` propose rules, measure ranking quality, and suggest weights. `judge.py` asks Apertus to rate candidates. See `src/suva/scoring/README.md`. |
| `src/suva/llm.py` | OpenAI-compatible Apertus calls, endpoint-scoped cache replay, and audit metadata. |
| `tests/` | Offline tests for the CLI, inputs, adapters, storage, parser, scoring, and Apertus client. HTTP and inference tests use test doubles. |

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
python -m suva coverage --start 2025-10-01 --end 2026-03-31
python -m suva parse
python -m suva dataset
python -m suva analyze
python -m suva evaluate
python -m suva fit-weights
```

The first fetch downloads the OpenCaseLaw snapshot and queries entscheidsuche. Use `--no-entscheidsuche` with an existing `data/raw/opencaselaw/bger.parquet` for an offline fetch. `fetch --window challenge --today YYYY-MM-DD` pins the reference date. For a live gateway check, set `LLM_NAME`, `LLM_BASE_URL`, and `LLM_API_KEY` using `.env.example`, then run `python -m suva llm-ping`. That command bypasses the cache. With the gateway set, `python -m suva judge --limit 30` rates the top candidates and `evaluate --with-judge` adds the judge's points. Scoring outputs go to `data/scoring/`.

## Invariants to preserve

- Use `docket@YYYY-MM-DD` when a decision date is available. Match dated labels exactly and surface ambiguous docket-only matches. Suva’s summary creation date is a separate field.
- Keep publication dates, decision dates, and retrieval timestamps distinct. Coverage describes the sources observed in a run and cannot establish that the court feed is complete.
- Keep source failures visible. Validate HTML and Parquet before replacing cached copies. Stop requests to a source after it refuses access.
- Preserve verbatim passage text and offsets into the stored judgement. Citation offsets are relative to the passage. Changes to source text invalidate derived rows. Bump `PARSER_VERSION` when segmentation or citation output changes.
- Keep SQLite migrations append-only and dataset replacement atomic. Keep frozen split assignments and source hashes reproducible, with decisions from the same docket in the same split.
- Scoring labels follow the same dated matching as fetch and parse. A dataset built from stale parses is reported, not silently used. Tune rules on the train split only. Use `evaluate --split test` for the final number. Bump `judge.PROMPT_VERSION` when the judge prompt changes.
- Keep `.env`, local databases, raw mirror caches, `data/scoring/` outputs, and generated manifests out of commits unless explicitly approved for sharing. Keep audit entries free of prompt text and API keys. Use a scratch database when replaying imports or parsing during review.
- Preserve the organizer’s paths and use [technical_report.md](technical_report.md) for submission run instructions and results.

## Review app

The review workspace is a Flask app (`src/app.py`) serving the React/TypeScript build in `src/frontend/`. Build it with `npm --prefix src/frontend ci && npm --prefix src/frontend run build` (Node.js 20.19+ or 22.12+), then run `.venv/bin/python src/app.py serve` and open http://127.0.0.1:5050.

- Draft German and French independently from the ruling and a shared factual brief, with examples in the target language. Keep review and approval explicit.
- Audio requires a separate approved summary per language. Apertus writes the script and cites original lines. Send only the script to ElevenLabs. Store and serve audio locally, and invalidate it when the approval, source, or generation settings change.
- Case conversation uses Apertus for answers and recap, with original ruling citations. ElevenLabs handles transcription and narration, separate from approved summaries. Keep recorded questions out of logs and storage.
- Run `node --test tests/conversation-ui.test.cjs` for the simulated browser and media flows. Python tests for the app run under `python -m pytest -q`.
