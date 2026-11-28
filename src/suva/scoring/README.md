# Relevance scoring

Gives every Federal Supreme Court judgement **points** from simple rules in
[`rules/rules.yaml`](../../../rules/rules.yaml) that experts can edit without touching code,
ranks the judgements by those points, and shows lawyers the **top 30 per month** (never below a
floor). Points are not a probability: only the order matters.

## The idea in one picture

```
judgements in the KB ──► dataset ──► analyze ──► rules.yaml ──► evaluate ──► reading list
   (fetch + parse)      (labels)   (find ideas)  (experts edit)  (how good?)
                                                      ▲              │
                                                      └── fix rules ─┘
```

1. **Labels come from the Excel.** For decisions from 1 Oct 2025 to 31 Mar 2026: in the Excel = relevant,
   not in the Excel = not relevant. (The `Export_*.html` summaries are only style examples for summarising,
   not labels.)
2. **Rules describe what relevant judgements look like**, e.g. "Suva is a party" or "subject is accident insurance".
3. **Points add up the rules that fire**; judgements are ranked by them.
4. **The reading list** is the top N per period above a floor (`selection` in `rules.yaml`).
5. **We measure** how many relevant judgements the reading list catches, and improve the rules.

## How points are calculated

```
rule points = points of every rule that fires
            + judge points                          (optional)
score       = base + rule points                    (display only)
```

Example (8C_129/2025):
`+51 accident insurance +32 chamber 8C +30 UVG articles +6 Suva party = 119 rule points`,
shown to lawyers as score `−95 + 119 = +24`.

Ranking, the selection floor and the judge gate use **rule points only**. `base` is the same
for every judgement, so it is added at the very end, for the score lawyers see.

## What lawyers see: the top N per period

```yaml
selection:
  top_n: 30               # judgements shown per period
  floor_rule_points: 66   # never shown below this many rule points (no base)
  per: month              # month | week | period (the whole input as one list)
```

Each month the 30 judgements with the most rule points are shown, most first. The **floor**
only shortens a month's list: in a quiet month, judgements below 66 rule points (a score of
−29 with base −95) are left out instead of filling all 30 places. It never adds judgements.
Equal points are ordered by key (in evaluation: not-relevant first, so ties never flatter the
numbers). The judge sees judgements with at least `judge.candidate_floor_rule_points` (also 66).

Why 30 per month: Suva picked about 8 judgements a month in the training period. The top 30
contain 87 % of them cross-validated (`cv`, refit) and 93 % with the current weights
(`evaluate`, equal points in random order). `evaluate` prints this trade-off for other values
of N, and `compare` prints the held-out confusion matrix (TP, FP, FN, TN) of the reading list.

The floor is in rule points, and rule points depend on the weights: when the weights change,
take the floor that `fit-weights` suggests (it keeps 90 % of the relevant training judgements).

## Significance rules

Topic rules find accident-insurance cases, but most of those are routine: only about 1 in 5
of the accident and invalidity cases in chambers 8C/9C is picked. Significance rules
(`group: significance` in `rules.yaml`) look at whether a case *matters*:

| Rule | Fires when | Picks / non-picks (train, hard cases) |
|---|---|---|
| `insurer_appeals` | an accident insurer (Suva or another) brought the appeal | 30 % / 2 % |
| `appeal_upheld` | the court upheld the appeal, fully or partly | 36 % / 14 % |
| `long_reasoning` | the reasoning runs to 4+ pages | 68 % / 35 % |
| `clarifies_case_law` | the court changes or clarifies its case law (DE/FR/IT wording) | 11 % / 2 % |

They use three fields derived in `derive.py` when the dataset is loaded: `appellant` (the
party before "gegen / contre / contro", without its lawyer), `outcome` (upheld,
partly_upheld, dismissed, not_entered, written_off, sent_back) and `reasoning_pages`.

Every judgement comes with its **explanation**: the rules that fired, with their points.

Similarity to past picks (shared citations, text embeddings) was tried and removed: it added
little on top of the significance rules. The numbers are in
[docs/pitch-stats.md](../../../docs/pitch-stats.md), "Tried and removed".

## Commands

Run from the repo root with the venv active. Outputs go to `data/scoring/`.

| Command | What it does |
|---|---|
| `python -m suva dataset` | One record per judgement with its label. March 2026 is held back as the **test set**. |
| `python -m suva analyze` | Lists words, laws, BGE citations etc. that are much more common in relevant judgements → **rule ideas**. |
| `python -m suva evaluate` | Ranks the training set and builds the reading list: recall and precision at N per month, a sweep over N, misses and false alarms; AP, AUC and "read to find 90 %" as secondary metrics. `--top-n`, `--floor-rule-points`, `--no-floor`, `--per` try other settings. |
| `python -m suva cv` | The same on judgements the weights were not fitted on: grouped 5-fold cross-validation (current weights, refit, shuffled labels, random). N is scaled to the fold size. |
| `python -m suva compare` | Fair before/after: fits weights per configuration (topic rules, then + significance rules) with 5-fold cross-validation, compares the rankings, and prints the held-out confusion matrix of the reading list. |
| `python -m suva fit-weights` | Suggests rule weights and a rule-points floor from the data → `rules/rules.fitted.yaml` (copy what you agree with). |
| `python -m suva judge --limit 30` | Optional: Apertus rates the candidates (rule points ≥ `judge.candidate_floor_rule_points`, no base) and gives a written reason. |
| `python -m suva evaluate --with-judge` | Ranks with the judge's points added. |
| `python -m suva evaluate --split test` | **Only at the end:** the honest result on unseen judgements. |

Needs `fetch --window dev` and `parse` to have run first.

## Improving the rules (the loop)

1. `evaluate` → look at **missed relevant judgements** and **false alarms**.
2. Find what they have in common (`analyze`, reading them, or asking Apertus).
3. Add or change a rule in `rules.yaml` (DE + FR + IT terms!).
4. `fit-weights` to get sensible points (copy the suggested floor with the weights), then `evaluate`
   again. Keep the change if recall at N improves. `cv` and `compare` show the gain on judgements the
   weights never saw, which is fairer than `evaluate` on train.

Don't tune on the test set, or its numbers stop meaning anything.

## Files

| File | Purpose |
|---|---|
| `dataset.py` | Builds the labelled dataset from the knowledge base |
| `analyze.py` | Counts features in relevant vs. not relevant → rule ideas |
| `rules.py` | Loads and checks `rules.yaml`, matches rules |
| `score.py` | Turns fired rules into points and an explanation (no LLM) |
| `select.py` | The reading list: top N per period above the floor |
| `evaluate.py` | Recall and precision at N, the N sweep, ranking metrics, misses, false alarms |
| `cv.py` | Grouped cross-validation with refit, shuffled-label and random baselines |
| `derive.py` | Appellant, outcome and reasoning length, derived from the dataset fields |
| `compare.py` | Cross-validated comparison of configurations (what each group of signals adds) |
| `fit.py` | Logistic regression that suggests weights and the rule-points floor |
| `judge.py` | Optional Apertus judge (cached, rubric in `rules.yaml`) |

## Good to know

- **Scoring is deterministic**: same judgement + same rules = same points. Apertus is only used in the optional judge.
- **Only the rank matters.** Points are not a probability: with ~60 relevant examples we cannot
  claim one. The weights come from a logistic regression, but they are used only to order judgements.
- **Ties:** with the current `rules.yaml` weights many judgements share the same points (123 training
  judgements at 73 rule points, a score of −22), so the cut at 30 falls inside a tie. `evaluate` reports recall both with ties
  ordered pessimistically (a lower bound) and in random order (what to expect).
- **Known limit:** rules find the right *topic*, but not which judgements are *significant*. Many
  accident-insurance cases with Suva as a party are not in the Excel. Next ideas: significance signals
  (leading-decision publication, five-judge panel, outcome) and a judge rubric focused on significance.
