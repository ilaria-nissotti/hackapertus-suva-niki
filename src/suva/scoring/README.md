# Relevance scoring

Gives every Federal Supreme Court judgement a **score from 0 to 100**: roughly the chance (in %)
that Suva would want to summarise it. The score comes from simple rules in
[`rules/rules.yaml`](../../../rules/rules.yaml) that experts can edit without touching code.

## The idea in one picture

```
judgements in the KB ──► dataset ──► analyze ──► rules.yaml ──► evaluate ──► scores
   (fetch + parse)      (labels)   (find ideas)  (experts edit)  (how good?)
                                                      ▲              │
                                                      └── fix rules ─┘
```

1. **Labels come from the Excel.** For decisions from 1 Oct 2025 to 31 Mar 2026: in the Excel = relevant,
   not in the Excel = not relevant. (The `Export_*.html` summaries are only style examples for summarising,
   not labels.)
2. **Rules describe what relevant judgements look like**, e.g. "Suva is a party" or "subject is accident insurance".
3. **The score adds up the points of the rules that fire** and turns them into 0–100.
4. **We measure** how well the scores match the Excel, and improve the rules.

## How a score is calculated

```
points = base + points of every rule that fires
score  = 100 / (1 + e^(-points / 10))
```

| points | −30 | −20 | −10 | 0 | +10 | +20 | +30 |
|---|---|---|---|---|---|---|---|
| score | 5 | 12 | 27 | 50 | 73 | 88 | 95 |

Example (8C_129/2025):
`−95 base +51 accident insurance +32 chamber 8C +30 UVG articles +6 Suva party = +24 points → score 92`.

Every score comes with its **explanation** (the rules that fired) and a **band**:
`high` (read it), `check` (skim it), `low` (skip it).

## Commands

Run from the repo root with the venv active. Outputs go to `data/scoring/`.

| Command | What it does |
|---|---|
| `python -m suva dataset` | One record per judgement with its label. March 2026 is held back as the **test set**. |
| `python -m suva analyze` | Lists words, laws, BGE citations etc. that are much more common in relevant judgements → **rule ideas**. |
| `python -m suva evaluate` | Scores the training set: top-10 precision, how far down the list all relevant ones are, misses, false alarms. |
| `python -m suva fit-weights` | Suggests rule weights from the data → `rules/rules.fitted.yaml` (copy what you agree with). |
| `python -m suva score` | Scores every judgement of the challenge window (last 3 months; `--window dev` for the dev window) and stores score, band and fired rules for the review app. |
| `python -m suva judge --limit 30` | Optional: Apertus rates the top candidates and gives a written reason. |
| `python -m suva evaluate --with-judge` | Scores with the judge's points added. |
| `python -m suva evaluate --split test` | **Only at the end:** the honest result on unseen judgements. |

Needs `fetch --window dev` and `parse` to have run first.

## Improving the rules (the loop)

1. `evaluate` → look at **missed relevant judgements** and **false alarms**.
2. Find what they have in common (`analyze`, reading them, or asking Apertus).
3. Add or change a rule in `rules.yaml` (DE + FR + IT terms!).
4. `fit-weights` to get sensible points, then `evaluate` again. Keep the change if the ranking improves.

Don't tune on the test set, or its numbers stop meaning anything.

## Files

| File | Purpose |
|---|---|
| `dataset.py` | Builds the labelled dataset from the knowledge base |
| `analyze.py` | Counts features in relevant vs. not relevant → rule ideas |
| `rules.py` | Loads and checks `rules.yaml`, matches rules |
| `score.py` | Turns fired rules into points, score, band, explanation (no LLM) |
| `evaluate.py` | Ranking metrics, calibration, misses, false alarms |
| `fit.py` | Logistic regression that suggests weights |
| `store.py` | Stores the scores in the KB and checks whether a stored score is still current |
| `judge.py` | Optional Apertus judge (cached, rubric in `rules.yaml`) |

## Good to know

- **Scoring is deterministic**: same judgement + same rules = same score. Apertus is only used in the optional judge.
- **Rank matters more than the exact number.** The score is approximate (only ~60 relevant examples).
- **Known limit:** rules find the right *topic*, but not which judgements are *significant*. Many
  accident-insurance cases with Suva as a party are not in the Excel. Next ideas: significance signals
  (leading-decision publication, five-judge panel, outcome) and a judge rubric focused on significance.
