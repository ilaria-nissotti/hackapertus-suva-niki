# Suva judgement ingestion and relevance scoring

This repository collects Swiss Federal Supreme Court judgements, reconciles them with Suva’s supplied examples, and turns judgement text into passages and citations. It gives each judgement relevance points from expert-editable rules and an optional Apertus judge, ranks judgements by those rule points, and selects the top N per month above a rule-points floor. The base is added only to the displayed score. It has an Apertus client and a gateway diagnostic. Bilingual summary generation and the judge-facing UI are still pending.

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
| `src/suva/scoring/`, `rules/rules.yaml` | `dataset.py` labels judgements in the dev window from the spreadsheet picks and splits them by time. `rules.py` loads and validates `rules.yaml`. `score.py` turns fired rules into points and an explanation without an LLM. `select.py` builds the reading list: the top N per period (month, week or the whole input), none below `floor_rule_points` (rule points, no base). `derive.py` derives the appellant, outcome, and reasoning length that the significance rules use. `analyze.py`, `evaluate.py`, and `fit.py` propose rules, measure recall and precision at N plus ranking quality, and suggest weights and a rule-points floor. `compare` also prints the held-out confusion matrix of the reading list. `cv.py` cross-validates by docket against refit, shuffled-label and random baselines. `compare.py` measures what the significance rules add over the topic rules with cross-validation on the train split. Similarity to past picks was tried and removed; see `docs/pitch-stats.md`. `judge.py` asks Apertus to rate candidates. See `src/suva/scoring/README.md`. |
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
python -m suva compare
python -m suva cv
```

The first fetch downloads the OpenCaseLaw snapshot and queries entscheidsuche. Use `--no-entscheidsuche` with an existing `data/raw/opencaselaw/bger.parquet` for an offline fetch. `fetch --window challenge --today YYYY-MM-DD` pins the reference date. For a live gateway check, set `LLM_NAME`, `LLM_BASE_URL`, and `LLM_API_KEY` using `.env.example`, then run `python -m suva llm-ping`. That command bypasses the cache. With the gateway set, `python -m suva judge --limit 30` rates the top candidates and `evaluate --with-judge` adds the judge's points. Scoring outputs go to `data/scoring/`.

## Invariants to preserve

- Use `docket@YYYY-MM-DD` when a decision date is available. Match dated labels exactly and surface ambiguous docket-only matches. Suva’s summary creation date is a separate field.
- Keep publication dates, decision dates, and retrieval timestamps distinct. Coverage describes the sources observed in a run and cannot establish that the court feed is complete.
- Keep source failures visible. Validate HTML and Parquet before replacing cached copies. Stop requests to a source after it refuses access.
- Preserve verbatim passage text and offsets into the stored judgement. Citation offsets are relative to the passage. Changes to source text invalidate derived rows. Bump `PARSER_VERSION` when segmentation or citation output changes.
- Keep SQLite migrations append-only and dataset replacement atomic. Keep frozen split assignments and source hashes reproducible, with decisions from the same docket in the same split.
- Scoring labels follow the same dated matching as fetch and parse. A dataset built from stale parses is reported, not silently used. Tune rules on the train split only. Rank by points; do not present them as a probability. Ranking, `selection.floor_rule_points` and `judge.candidate_floor_rule_points` use rule points without `base`; `base` is only added to the displayed score. A rule-points floor belongs to one set of weights, so re-derive it with `fit-weights` when weights change. Use `evaluate --split test` for the final number. Bump `judge.PROMPT_VERSION` when the judge prompt changes.
- Keep `.env`, local databases, raw mirror caches, `data/scoring/` outputs, and generated manifests out of commits unless explicitly approved for sharing. Keep audit entries free of prompt text and API keys. Use a scratch database when replaying imports or parsing during review.
- Preserve the organizer’s paths and use [technical_report.md](technical_report.md) for submission run instructions and results. [docs/pitch-stats.md](docs/pitch-stats.md) collects the numbers behind the scoring choices; update it when `compare` results change.
