# Suva judgement review

This repository collects Swiss Federal Supreme Court judgements, reconciles them with Suva’s supplied examples, and turns judgement text into passages and citations. It gives each judgement relevance points from expert-editable rules and an optional Apertus judge, ranks judgements by those rule points, and selects the top N per month above a rule-points floor. The base is added only to the displayed score. A local review app shows the reading list as a ranked queue, drafts German and French summaries in Suva’s style with Apertus, and records reviewer approval. Approved summaries can be narrated, and reviewers can ask questions about a judgement (ElevenLabs).

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
| `src/suva/scoring/`, `rules/rules.yaml` | `dataset.py` labels judgements in the dev window from the spreadsheet picks and splits them by time. `rules.py` loads and validates `rules.yaml`. `score.py` turns fired rules into points and an explanation without an LLM. `select.py` builds the reading list: the top N per period (month, week or the whole input), none below `floor_rule_points` (rule points, no base). `derive.py` derives the appellant, outcome, and reasoning length that the significance rules use. `analyze.py`, `evaluate.py`, and `fit.py` propose rules, measure recall and precision at N plus ranking quality, and suggest weights and a rule-points floor. `compare` also prints the held-out confusion matrix of the reading list. `cv.py` cross-validates by docket against refit, shuffled-label and random baselines. `compare.py` measures what the significance rules add over the topic rules with cross-validation on the train split. Similarity to past picks was tried and removed; see `docs/pitch-stats.md`. `store.py` stores the display score, rule points and reading-list status per judgement for the app (`suva score`). `feedback.py` retrains the weights with the reviewers' verdicts for the app's Rule weights screen and writes accepted weights into `rules.yaml`. `judge.py` asks Apertus to rate candidates. See `src/suva/scoring/README.md`. |
| `src/suva/llm.py` | OpenAI-compatible Apertus calls, endpoint-scoped cache replay, and audit metadata. |
| `src/app.py`, `jobs.py`, `review_store.py`, `review_kb.py` | Flask review app. `app.py` holds the routes and startup checks. `jobs.py` runs the summary pipeline, the job queue and briefing jobs, without Flask. `review_store.py` owns every query on the app’s own `DATA_DIR/review.sqlite` (drafts, approvals and their signatures, briefings, hand-added cases, verdicts, weight proposals). `review_kb.py` reads judgements, parses and stored scores from the KB. |
| `src/summaries.py`, `bger.py` | Factual brief and independent DE/FR drafts with few-shot Suva examples. `bger.py` holds config and the Apertus chat call. |
| `src/suva/glossary.py`, `rules/glossary.yaml` | Expert-editable DE/FR term pairs: passed to the draft prompt, and other-language terms in a draft are flagged as `glossary_warnings`. |
| `src/library_ask.py`, `case_search.py`, `export.py` | Library questions over approved summaries (FTS5 in `review.sqlite`, Apertus answer with cited cases). Precedent search for a case description (BM25 plus citation boosts, no LLM). HTML/Word/PDF export (`export.py`) of approved DE/FR summaries in the shape of Suva's `Export_*.html`. |
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
python -m suva compare
python -m suva cv
```

The first fetch downloads the OpenCaseLaw snapshot and queries entscheidsuche. Use `--no-entscheidsuche` with an existing `data/raw/opencaselaw/bger.parquet` for an offline fetch. `fetch --window challenge --today YYYY-MM-DD` pins the reference date; `score` and `src/app.py` take the same `--today`. For a live gateway check, set `LLM_NAME`, `LLM_BASE_URL`, and `LLM_API_KEY` using `.env.example`, then run `python -m suva llm-ping`. That command bypasses the cache. With the gateway set, `python -m suva judge --limit 30` rates the top candidates and `evaluate --with-judge` adds the judge's points. Scoring outputs go to `data/scoring/`.

## Invariants to preserve

- Use `docket@YYYY-MM-DD` when a decision date is available. Match dated labels exactly and surface ambiguous docket-only matches. Suva’s summary creation date is a separate field.
- Keep publication dates, decision dates, and retrieval timestamps distinct. Coverage describes the sources observed in a run and cannot establish that the court feed is complete.
- Keep source failures visible. Validate HTML and Parquet before replacing cached copies. Stop requests to a source after it refuses access.
- Preserve verbatim passage text and offsets into the stored judgement. Citation offsets are relative to the passage. Changes to source text invalidate derived rows. Bump `PARSER_VERSION` when segmentation or citation output changes.
- Keep SQLite migrations append-only and dataset replacement atomic. Keep frozen split assignments and source hashes reproducible, with decisions from the same docket in the same split.
- Scoring labels follow the same dated matching as fetch and parse. A dataset built from stale parses is reported, not silently used. Tune rules on the train split only. Rank by points; do not present them as a probability. Ranking, `selection.floor_rule_points` and `judge.candidate_floor_rule_points` use rule points without `base`; `base` is only added to the displayed score. A rule-points floor belongs to one set of weights, so re-derive it with `fit-weights` when weights change. Use `evaluate --split test` for the final number. Bump `judge.PROMPT_VERSION` when the judge prompt changes. The app shows stored scores and flags them as stale when the rules, selection, parse or text changed; run `suva score` again. Bump `store.SCORER_VERSION` when the scorer gives different points for the same rules. Reviewer verdicts retrain the weights only when an expert accepts a proposal on the Rule weights screen; verdicts on test-split dockets are never used, and accepting changes only the numbers in `rules.yaml` (comments kept) and rescores the window.
- Keep `.env`, local databases (including `review.sqlite`), `.suva-work/`, `src/frontend/dist/`, raw mirror caches, `data/scoring/` outputs, and generated manifests out of commits unless explicitly approved for sharing. Keep audit entries free of prompt text and API keys. Use a scratch database when replaying imports or parsing during review.
- Preserve the organizer’s paths and use [technical_report.md](technical_report.md) for submission run instructions and results. [docs/pitch-stats.md](docs/pitch-stats.md) collects the numbers behind the scoring choices; update it when `compare` results change.

## Review app

Build the frontend with `npm --prefix src/frontend ci && npm --prefix src/frontend run build` (Node.js 20.19+ or 22.12+). After `fetch`, `parse` and `score`, run `.venv/bin/python src/app.py serve` and open http://127.0.0.1:5050. `src/app.py summarize-top --limit 10` drafts summaries for the best-scored judgements of the window.

- The app asks who is reviewing first (name, initials, optional function; no accounts or passwords) and keeps it in the signed session cookie. Every route but the page shell and `/api/session` waits for it. Approvals and assessments are signed with the session's reviewer, never with names sent by the client; keep signatures append-only. Tests act as a fictional reviewer (`tests/conftest.py`); the browser tests enter one in `e2e/auth.setup.ts`.
- The app does not scrape or score. Judgements come from `suva fetch` and scores from `suva score`. Identify cases by their `docket@date` key; a hand-added docket with several decisions needs its decision date.
- Take style examples only from the few-shot split. Keep the evaluation split unseen by prompts.

- Draft German and French independently from the ruling and a shared factual brief, with examples in the target language. Keep review and approval explicit.
- Audio requires a separate approved summary per language. Apertus writes the script and cites original lines. Send only the script to ElevenLabs. Store and serve audio locally, and invalidate it when the approval, source, or generation settings change.
- Case conversation uses Apertus for answers and recap, with original ruling citations. ElevenLabs handles transcription and narration, separate from approved summaries. Keep recorded questions out of logs and storage.
- Run `node --test tests/conversation-ui.test.cjs` for the simulated browser and media flows.
- Run `npm --prefix src/frontend run test:ui` after a frontend build for the browser tests: axe WCAG 2.1 A/AA scans, keyboard checks and screenshots at 1440×900, against fictional data served by `scripts/ui_fixture.py` (never `data/`). Needs `npx playwright install chromium` once. After an intended visual change, review the diff and update the baselines with `npx playwright test --update-snapshots`. Python tests for the app run under `python -m pytest -q`.
