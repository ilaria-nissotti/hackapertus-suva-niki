# Technical report — `project name`, `team name`

A deeper write-up than the README: what you built, how it works, and what the
numbers say.

- **Challenge partner:** Suva
- **Challenge:** Identification of relevant Federal Court judgements and preparation of a legal summary
- **Event:** On-site Zurich
- **Team:** `team name` — `member`, `member`, `member`
- **Demo:** `link to video`

## 1. Summary

The prototype helps a Suva reviewer prioritize Swiss Federal Supreme Court
judgments and draft independent German and French summaries. It retains the
original judgment and shows the evidence behind each high score.

## 2. Architecture

The React and TypeScript workspace is built with Vite and served by Flask. The browser reads a same-origin dashboard API and refreshes active jobs in place. Flask and SQLite run locally. Node.js is needed at build time. A resumable Python
collector downloads records and full text from OpenCaseLaw and preserves the
publication date separately from the ruling date. JSONL import supports later
datasets. Apertus tasks run server-side, and the browser sees only the saved
case data and results.

### Target architecture (mandatory)

State which of the three architectures your project is deployable in, and how
it meets that constraint:

- **a) On-premise** — on the organisation's own infrastructure, under its own administration.
- **b) Air-gapped** — with no external network connection at runtime.
- **c) Sovereign Swiss cloud** — on a cloud platform operated in Switzerland, under Swiss jurisdiction, with Swiss data residency.

List any external dependencies, and separate build time from runtime.

## 3. Use of Apertus

- **Model:** Apertus v1.5 8B (`apertus-v1-5-8b`).
- **Use:** inference for triage, evidence verification, factual briefs and drafts.
- **Endpoint:** Keera Gateway, configured using the repo's `LLM_*` variables.

The score uses ordinal anchors 0, 20, 45, 70 and 95. The model selects numbered
lines and the application resolves them directly from the source. High scores
require an accident-insurance signal and a second inference check. Each summary
uses the source, a shared factual brief and up to three same-language examples.
Reviewer-approved edits can become examples in subsequent requests.

## 4. Data

What you used, where it came from, and its licence. Flag anything personal or
non-redistributable, and keep it out of the repository (see `.gitignore`).
If data comes from human subjects or contains personal information, describe
how consent was obtained.

## 5. Limitations

Mirror publication dates do not establish complete Q2 coverage. A source line
can be authentic while the model's interpretation is wrong. The evidence gate
can send relevant judgments to Review, particularly when the selected lines do
not contain a recognized term. The Excel labels positive examples and cannot by
itself establish false-positive rates. Drafts require legal review.

## 6. Reproducibility

Use the README commands for setup, sync, scoring and local launch.
`src/evaluate.py` separates Q4 development positives from Q1 holdout positives,
uses fixed negative cases, and reports our results and failed tasks as JSON.
The small `--positive-limit` run checks the pipeline and is not a population
estimate. Inference results and approvals are stored locally in SQLite.

The first full historical benchmark selected a shortlist cutoff of 45 on
Q4 development cases. It recovered 23 of 31 Q1 holdout positives (74.2% recall,
F2 0.782 on this sample), and neither of the two held-out negatives was
shortlisted. The two failed holdout requests count as missed positives.
Two workbook rows without a directly parsable federal docket were excluded.
The two negatives are regression examples from earlier prompt experiments.
This small sample cannot estimate production precision.

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
