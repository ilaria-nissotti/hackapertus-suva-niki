# Technical report — `project name`, `team name`

A deeper write-up than the README: what you built, how it works, and what the
numbers say.

- **Challenge partner:** Suva
- **Challenge:** Identification of relevant Federal Court judgements and preparation of a legal summary
- **Event:** On-site Zurich
- **Team:** `team name` — `member`, `member`, `member`
- **Demo:** `link to video`

## 1. Summary

The problem, your approach, and the headline result in one paragraph.

## 2. Architecture

Components, data flow, and where each one runs. Put diagrams in `docs/` and
reference them here.

### Target architecture (mandatory)

State which of the three architectures your project is deployable in, and how
it meets that constraint:

- **a) On-premise** — on the organisation's own infrastructure, under its own administration.
- **b) Air-gapped** — with no external network connection at runtime.
- **c) Sovereign Swiss cloud** — on a cloud platform operated in Switzerland, under Swiss jurisdiction, with Swiss data residency.

List any external dependencies, and separate build time from runtime.

## 3. Use of Apertus

- **Model:** `e.g. swiss-ai/Apertus-v1.5-8B`
- **How it is used:** inference | fine-tuning | evaluation | red-teaming | agents / tool use
- **Where it runs:** `local weights, hosted endpoint, ...`

Prompts, adapters, quantisation, serving stack — whatever a reader needs to
rebuild your setup.

## 4. Data

What you used, where it came from, and its licence. Flag anything personal or
non-redistributable, and keep it out of the repository (see `.gitignore`).
If data comes from human subjects or contains personal information, describe
how consent was obtained.

## 5. Limitations

Where it breaks, what you did not test, and known failure modes.

## 6. Reproducibility

What a judge needs to get your numbers back: hardware, runtime, seeds, and the
exact commit.

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
