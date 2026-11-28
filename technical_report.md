# Technical report — `project name`, `team name`

A deeper write-up than the README: what you built, how it works, and what the
numbers say.

- **Challenge partner:** Suva
- **Challenge:** Identification of relevant Federal Court judgements and preparation of a legal summary
- **Event:** On-site Zurich
- **Team:** `team name` — `member`, `member`, `member`
- **Demo:** `link to video`

## 1. Summary

The prototype collects Swiss Federal Supreme Court judgments published in the
last three months and scores each one for relevance to Suva with deterministic,
expert-editable rules. Every score comes with the rules that fired, so a reviewer
sees why a judgment was selected or not. For the shortlisted judgments, Apertus
drafts independent German and French summaries in Suva's style, which a reviewer
edits and approves in a local web workspace. Approved summaries can be narrated
(ElevenLabs), and reviewers can ask questions about a judgment with answers
citing the original lines.

## 2. Architecture

One data pipeline feeds one review workspace:

1. **Collect** (`python -m suva fetch --window challenge`): the OpenCaseLaw
   Parquet snapshot plus entscheidsuche listings and HTML, keyed by
   `docket@decision-date`. Publication, decision and retrieval dates stay
   separate, and a coverage ledger records which source listed each decision.
2. **Parse** (`suva parse`): verbatim passages (header, regeste, facts,
   considerations, ruling) and citations (statutes, BGE, dockets) with offsets.
3. **Score** (`suva score`): `rules/rules.yaml` turns fired rules into rule
   points and an explanation, and the reading list takes the top 30 per month
   above a rule-points floor. The app shows the listed judgments as Relevant,
   others above the floor as Review, and the rest as Low relevance. No language
   model is involved, so the same judgment and rules always give the same result.
4. **Review** (`src/app.py serve`): a Flask app serves the React/TypeScript
   build (Vite; Node.js only at build time). It reads judgments and scores from
   the knowledge base (`data/kb.sqlite`) and keeps drafts, approvals and audio
   in its own `data/review.sqlite`. Apertus tasks run server-side; the browser
   sees only saved case data and results. The reviewer enters name, initials and
   function when opening the app; every approval is signed with them, and the
   export prints Suva's "Autor" line.

### Target architecture (mandatory)

State which of the three architectures your project is deployable in, and how
it meets that constraint:

- **a) On-premise** — on the organisation's own infrastructure, under its own administration.
- **b) Air-gapped** — with no external network connection at runtime.
- **c) Sovereign Swiss cloud** — on a cloud platform operated in Switzerland, under Swiss jurisdiction, with Swiss data residency.

List any external dependencies, and separate build time from runtime.

## 3. Use of Apertus

- **Model:** Apertus v1.5 8B (`apertus-v1-5-8b`).
- **Use:** inference for factual briefs, DE/FR drafts, briefing scripts and case
  questions. The relevance score itself is deterministic; an optional Apertus
  judge (`suva judge`) can rate candidates for evaluation.
- **Endpoint:** Keera Gateway, configured using the repo's `LLM_*` variables.

Each summary uses the source, a shared factual brief and up to three
same-language Suva examples from the few-shot split (the evaluation split is
never shown to the model). The model selects numbered lines and the application
resolves them directly from the source. The frozen few-shot split is the sole
source of style examples.

## 4. Data

What you used, where it came from, and its licence. Flag anything personal or
non-redistributable, and keep it out of the repository (see `.gitignore`).
If data comes from human subjects or contains personal information, describe
how consent was obtained.

## 5. Limitations

Mirror publication dates do not establish complete coverage of the window, and
some decisions have no known publication date (they are included and counted).
The rules find the right topic but not always the significant judgments: many
accident-insurance cases with Suva as a party are not in Suva's selection. The
Excel labels positive examples and cannot by itself establish false-positive
rates. A source line can be authentic while the model's interpretation is wrong.
Drafts require legal review.

## 6. Reproducibility

Use the README commands for setup, fetch, parse, scoring and local launch.
`python -m suva dataset` labels the dev window (2025-10-01 to 2026-03-31) from
Suva's spreadsheet and holds back March 2026 as the test split (dockets stay in
one split). `python -m suva evaluate` reports ranking metrics on the training
split; `evaluate --split test` gives the final number. On the training split the
current rules reach top-10 precision 70%, average precision 0.52, AUC 0.991, and
100% recall at the threshold (181 flagged, 47 relevant).

*Earlier experiment (replaced):* an Apertus ordinal triage (0/20/45/70/95 with
verified evidence quotes) recovered 23 of 31 Q1 holdout positives (74.2% recall,
F2 0.782) at a cutoff of 45. That sample was small and the triage cost one or
two model calls per judgment, so the product now uses the deterministic rules.

### Running it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt && pip install -e .
cp .env.example .env          # set LLM_NAME, LLM_BASE_URL, LLM_API_KEY
python -m suva init-db        # create/upgrade the knowledge base (data/kb.sqlite)
python -m suva llm-ping       # one live call: checks the Apertus endpoint, prints latency/tokens
pytest                        # offline test suite (no network, fake LLM)
```

Configuration is environment-only (`.env.example`). `LLM_CACHE_MODE=readonly`
replays cached Apertus calls without network access (frozen snapshot mode); every
LLM call is recorded in the append-only `audit_log` table (model, host, prompt
version, input/output hashes — no prompt text, no keys).

Open **Coverage** in the review queue to filter imported judgments by their
known publication dates. Type the dates as `YYYY-MM-DD`. The filter includes
both endpoints and keeps the range in the URL. To load and score a historical
period, pass the same dates to both commands.

```bash
python -m suva fetch --published-from 2026-04-01 --published-to 2026-06-30 --no-picks
python -m suva parse
python -m suva score --published-from 2026-04-01 --published-to 2026-06-30
```

To import the supplied summaries and picked-judgement workbooks, run
`python -m suva import-supplied`. Each successful import replaces the supplied
dataset together. Once a summary split is frozen, use a fresh database for a
changed summary corpus.

Run `python -m suva split-examples --manifest split-manifest.json` to save the
versioned split and source hashes. Pass the same manifest after importing into
a fresh database to reproduce the holdout. New splits group decisions by docket
and include each source language when the holdout size allows. Existing complete
splits keep their original assignments.


## License

Creative Commons Attribution 4.0 (CC-BY-4.0). All HackApertus projects are open-sourced.

## References
