# Pitch statistics: relevance scoring

Numbers that explain why the scoring pipeline looks the way it does, and show that each step
improves it. Each number names the command that produced it, so it can be re-run.
`python docs/pitch_stats.py` recomputes the ones no CLI command prints (add `--cv` for the
cross-validated case ranks); "§n" refers to the section of its output. Unless stated otherwise, figures come from the **training split** (decisions
1 Oct 2025 – 28 Feb 2026). The March 2026 test split has not been used for any decision.

Last updated: 27 Nov 2026 (all numbers regenerated).

---

## 1. The problem in numbers

| Fact | Value | Source |
|---|---|---|
| Federal Supreme Court judgements in the dev window (Oct 2025 – Mar 2026) | **3,936** | `docs/pitch_stats.py` §1 |
| …of which Suva chose to summarise (spreadsheet) | **59** (47 train, 12 test) | `docs/pitch_stats.py` §1 |
| Share Suva picks | **1.5 %**, about 1 in 67 | `docs/pitch_stats.py` §1 |
| Picks decided by a social insurance division (8C/9C) | **47 / 47** | `evaluate`, rule evidence |
| Accident or invalidity insurance cases in 8C/9C (train) | **229**, of which 47 picks (21 %) | `docs/pitch_stats.py` §1 |

**Message:** finding the right topic is easy; deciding *which* on-topic judgements matter is
the hard part. Every pick sits in a pool of 229 similar-looking cases, and about 4 in 5 of
those are not picked.

## 2. Step 1: topic rules already remove 94 % of the reading

Topic rules check things like: the subject is accident insurance, a social insurance
division decided it, UVG articles are cited, and the appeal was admissible.

| Score = base + rule points (current `rules.yaml`) | Judgements | Picks among them |
|---|---|---|
| 0 or more | 36 | 25 |
| −29 to below 0 | 145 | 22 |
| below −29 | 3,028 | **0** |

*Source: `python docs/pitch_stats.py` §2. The significance weights in `rules.yaml` are
still 0, so only the topic rules score.*

- A lawyer reads **181 of 3,209 judgements (5.6 %)** and sees **every pick**.
- No pick has a score below −22 (73 rule points).
- **But** only 31 distinct point values exist, so cases tie and the order inside the
  shortlist is close to arbitrary. False alarms such as 8C_151/2025 and 8C_384/2025 rank
  **2nd and 3rd** *(ties broken pessimistically)*.

## 3. Why we added *significance*, not more topic rules

We compared the 229 hard cases (47 picks vs 182 non-picks) on candidate signals.

| Signal | Picks | Non-picks | Used? |
|---|---|---|---|
| **An insurer appealed** (Suva or another accident insurer) | **30 %** | **2 %** | yes: `insurer_appeals` |
| …Suva itself appealed | 13 % | 0 % | part of the above |
| **Appeal upheld (fully or partly)** | **36 %** | **14 %** | yes: `appeal_upheld` |
| Sent back to the lower court | 28 % | 13 % | overlaps with "upheld" |
| **Reasoning ≥ 4 pages** | **68 %** | **35 %** | yes: `long_reasoning` |
| Court changes or clarifies case law (DE/FR/IT wording) | 11 % | 2 % | yes, rare: `clarifies_case_law` |
| Appeal not entered (inadmissible) | 0 % | 27 % | already a topic rule |
| IV office appealed | 4 % | 5 % | no: no signal |
| Cites recent leading decisions (BGE vol. 150/151) | 28 % | 40 % | no: slightly the wrong way |
| Published as a leading decision (BGE) | 0 % | 0 % | no: publication comes months later |

*Source: `python docs/pitch_stats.py` §3, with the fields derived in `src/suva/scoring/derive.py`.*

**Message:** Suva picks judgements where the law moves: the insurer fights a cantonal
ruling, the Federal Supreme Court corrects the lower court, the reasoning is substantial.
These are legally meaningful signals, not keyword tricks.

## 4. Each step improves the ranking (cross-validated)

Weights are re-fitted for each configuration on 4/5 of the training dockets, and
judgements are scored on the remaining 1/5, five times over. So every judgement is scored
by weights that never saw its label.

| Configuration | Avg. precision | Picks in top 10 | Picks in top 20 | Read to find 90 % of picks |
|---|---|---|---|---|
| Topic rules | 0.56 | 7 | 12 | 160 |
| + significance rules | **0.64** | **9** | **14** | 155 |

*Source: `python -m suva compare` → `data/scoring/comparison.json`.*

The reading list (top 30 per month) on the held-out folds only, summed over the 5 folds:

| | Selected | Not selected |
|---|---|---|
| Picked by Suva | **38** (TP) | 9 (FN) |
| Not picked by Suva | 72 (FP) | 3,090 (TN) |

Precision 38 / 110 = **35 %**, recall 38 / 47 = **81 %**. With topic rules alone: 40 found,
94 false alarms (30 % precision, 85 % recall). *Source: `python -m suva compare` (each fold
gets its share of a month's 30 places; floor fitted per fold: 65, 76, 73, 81, 79 rule points).*

- **Average precision +14 %** (0.56 → 0.64), from the significance rules.
- **Top 20 goes from 12 to 14 picks**, out of 47 in total. That is the part of the list
  lawyers actually read first.
- **22 fewer false alarms** in the held-out reading lists (94 → 72), for 2 more missed picks.
- **Granularity:** 31 → **72** distinct point values, so fewer ties *(weights fitted once on
  all of train; `docs/pitch_stats.py` §5)*.
- **Still open:** the "read to find 90 %" figure barely moves (160 → 155). The last picks are
  invalidity cases that no significance rule targets yet.

### Concrete cases (same cross-validated runs: topic rules → + significance rules)

| Judgement | Picked? | Rank before | Rank after | Why it moved |
|---|---|---|---|---|
| 8C_384/2025 (assurance-accidents, pension) | no | 3 | 19 | on topic, but nothing signals significance |
| 8C_151/2025 (Unfallversicherung, integrity compensation) | no | 5 | 14 | same |
| 8C_314/2025 (assurance-invalidité, helplessness allowance) | **yes** | 158 | **50** | appeal upheld, long reasoning, clarifies case law |
| 9C_65/2025 (assicurazione invalidità) | **yes** | 131 | **34** | same |
| 8C_351/2025 (assurance-accidents, admissibility) | **yes** | 54 | **24** | the insurer appealed and won |

*Source: `python docs/pitch_stats.py --cv` §5.*

## 5. Why rules, and the LLM only as a second opinion

- **Apertus as the only judge doesn't discriminate yet:** it rated all **25 of 25**
  candidates "high", including **7 non-picks**. *(Source: `data/scoring/judge_train.jsonl`
  from `judge --limit 30` with an older rubric; counted by `docs/pitch_stats.py` §6.)*
- **Rules explain every rank.** Each judgement lists the rules that fired with their points,
  e.g. "The insurer appealed …". Lawyers can check and correct the reasons.
- **Experts edit `rules.yaml` without touching code**, and `fit-weights` suggests the
  points from data.
- **Planned feedback loop:** each expert verdict becomes a new label, and `fit-weights`
  re-weighs the rules with it.

## 6. Caveats to state openly

- **There are only 47 picks for training** (`docs/pitch_stats.py` §1). Differences of one or two picks in the top 20
  are within noise.
- Signals were chosen by looking at the training data, so part of the gain may be luck.
  The test split (12 picks) gives the final honest number.
- **The labels are a single spreadsheet.** A judgement Suva didn't summarise may still have
  been relevant.

## Reading budget: top 30 per month

Points are used only to rank; lawyers read the top 30 judgements per month, never below
66 rule points, a score of −29 (`selection` in `rules.yaml`). Suva picked about 8 judgements a month in training
(`evaluate`, per-period table).

| Top N per month | Recall (ties random) | Precision | Recall (ties pessimistic) |
|---|---|---|---|
| 10 | 61 % | 57 % | 60 % |
| 20 | 82 % | 39 % | 72 % |
| **30** | **93 %** | **29 %** | 79 % |
| 40 | 100 % | 26 % | 98 % |

*Source: `evaluate` on train with the current `rules.yaml` weights (optimistic: tuned on train).*

Cross-validated (`cv`, 5 folds × 5 seeds, N scaled to fold size, ties random):

| Mode | Recall at N | Precision at N | AP (ties) |
|---|---|---|---|
| current weights | 94 % | 29 % | 0.64 |
| **refit (honest)** | **87 %** | **33 %** | **0.71** |
| shuffled labels | 14 % | 4 % | 0.06 |
| random | 4 % | 1 % | 0.02 |

- **Message:** a reading list of 30 a month finds about 9 in 10 of the judgements Suva picks.
- The gap between pessimistic and random tie order comes from many judgements sharing the same
  points (123 at a score of −22 with the current weights; `docs/pitch_stats.py` §2). Giving the significance rules weight would break
  most of these ties.

## Tried and removed: similarity to past picks

We tried scoring judgements by how much they resemble the ones Suva picked before, with two
signals: shared citations and text embeddings. Both were measured, found to add little on top
of the significance rules, and removed from the code (`similarity.py`, `embed.py`, the
`embed` command and the `embeddings` dependency). The numbers below are kept as the record of
that decision. They came from the removed code, so they can no longer be re-run; the
command named with each one is the one that produced it at the time.

### Why we tried it

- **The citation pattern of picks differs.** Average citation similarity to past picks:
  **0.29 for picks vs 0.08 for the rest**. *(Source: `evaluate`, similarity evidence.)*
- **Raw citation counts would mislead.** The procedural provision Art. 66 Abs. 1 BGG (court
  costs) appears in **3,099 of 3,936** judgements (79 %) *(still reproducible:
  `docs/pitch_stats.py`, "tried and removed")*. So similarity weighted rare citations up
  (TF-IDF) and compared at article level.
- **Honest by design:** a judgement was never compared with a pick from its own docket, and
  test judgements only with training picks.
- **Embedding similarity:** a local open-source model (BAAI/bge-m3, MIT licence) read each
  judgement. It works across German, French and Italian, and no text left the machine.
  3,935 of the 3,936 judgements were embedded, on a laptop GPU *(`docs/pitch_stats.py` §4)*.

### What it showed: meaning captures topic, not significance

| Signal | All judgements: picks vs rest | Hard cases (accident/invalidity, 8C/9C): picks vs rest |
|---|---|---|
| Citation similarity | 0.29 vs 0.08 | 0.29 vs 0.22 |
| Embedding similarity | 0.82 vs 0.66 | **0.82 vs 0.81** |

*Source: `evaluate` similarity evidence (all judgements); `docs/pitch_stats.py` §4 (the 229
hard cases).*

Embeddings recognise *what a case is about* very well. But among cases on the same topic,
picks and non-picks read alike (0.82 vs 0.81). Suva's choice depends on whether the law
moves, which is what the significance rules capture.

### Cross-validated gain at the time

| Configuration | Avg. precision | Picks in top 10 | Picks in top 20 | Read to find 90 % of picks |
|---|---|---|---|---|
| Topic rules | 0.56 | 7 | 12 | 160 |
| + citation similarity | 0.59 | 8 | 13 | 152 |
| + significance rules | **0.66** | **8** | **15** | 161 |
| + embedding similarity | 0.67 | 9 | 15 | 159 |
| *topic + significance only* | 0.65 | 8 | 15 | 167 |
| *topic + embedding only* | 0.58 | 7 | 13 | 160 |

*Source: `python -m suva compare` → `data/scoring/comparison.json`, before the removal (and
before base was taken out of ranking, which shifts these pooled numbers slightly).*

- **Citation similarity added +0.03 AP** (0.56 → 0.59); `fit-weights` suggested +17 points at
  similarity 1.0.
- **Embeddings added +0.01 AP and one pick in the top 10.** With 47 picks, that is within
  noise, and `fit-weights` gave the embedding signal about zero weight (−3) once the other
  signals were present.
- **Granularity with all signals:** 31 → 535 distinct point values *(`docs/pitch_stats.py` §5
  at the time)*.

### Where embeddings could still help

They didn't help scoring, but they suit other parts of the challenge:

- **A searchable knowledge base** (challenge step 3): "find judgements like this one" across
  DE/FR/IT.
- **Showing the closest past summaries in the UI**, as context for lawyers and as style
  examples for the summariser.
- **A feedback loop**: once experts label more cases, similarity to *their* picks may
  separate better than similarity to 47 spreadsheet rows.
