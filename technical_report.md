# Technical report — Suva AI, APerturbUs

- **Challenge partner:** Suva
- **Challenge:** Identification of relevant Federal Court judgements and preparation of a legal summary
- **Event:** On-site Zurich
- **Team:** APerturbUs — Ilaria Nissotti Revel, Ismaël Markria, Nicolás Cárdenas
- **Repository:** https://github.com/ilaria-nissotti/hackapertus-suva-niki
- **Demo:** https://youtu.be/riUa2xmDsmQ

## 1. Summary

Suva's lawyers read new Federal Supreme Court judgements every month to find the
few that matter for accident insurance, and summarise those in German and French.
Our prototype automates the search and the first draft, and leaves the legal
judgement with the lawyer.

1. **Collect.** It loads every judgement of the last three months into a local
   knowledge base and splits each one into verbatim passages and citations.
2. **Rank.** It scores each judgement with rules that experts can read and edit
   (`rules/rules.yaml`). Every score lists the rules that fired, so a lawyer sees
   why a judgement was or was not selected. No language model decides relevance.
3. **Draft.** For the judgements a lawyer opens, Apertus writes a German and a
   French summary independently, in Suva's style, from the ruling and a shared
   factual brief whose quotes are checked against the source.
4. **Review.** A local web app shows the ranked queue, the reasons, the original
   ruling and both drafts. A lawyer edits and approves each language; approvals
   are signed with the reviewer's name, and approved summaries can be searched,
   asked about and exported in the format of Suva's own summaries.

In cross-validation on Suva's past picks, a reading list of 30 judgements per
month finds **87 %** of the judgements Suva chose to summarise, while a lawyer
reads about 5 % of all judgements. A German and French summary costs about
**29,000 Apertus tokens** and one minute of model time per judgement.

## 2. Architecture

One data pipeline (`python -m suva …`) feeds one review app (`src/app.py`).

| Step | Command | What it does |
|---|---|---|
| Import examples | `import-supplied`, `split-examples` | Reads Suva's 55 bilingual HTML summaries and the spreadsheet of picked judgements. Freezes a split: 40 summaries are style examples for prompts, 15 are held out and never shown to the model. |
| Collect | `fetch --window challenge` | Loads judgements from the OpenCaseLaw snapshot and entscheidsuche.ch, keyed by `docket@decision-date`. Publication, decision and retrieval dates stay separate; a coverage report shows which weeks each source listed. Error pages are rejected before caching. |
| Parse | `parse` | Splits each judgement into verbatim passages (header, regeste, facts, considerations, ruling) with character offsets, and extracts statutes, leading decisions (BGE) and docket references. |
| Score | `score` | Applies the rules and stores the points, the fired rules and the reading-list status (Relevant, Review, Low relevance) per judgement. |
| Review | `src/app.py serve` | Flask serves a React/TypeScript interface on `127.0.0.1`. Apertus calls run on the server; the browser only sees stored case data and results. |

**Scoring.** Rules come in two groups. *Topic rules* check that a judgement is
about accident or invalidity insurance, comes from a social-insurance division
(8C/9C), cites the UVG and was admissible. *Significance rules* capture what
separates Suva's picks from other cases on the same topic: an insurer appealed,
the appeal was upheld, the reasoning is long, or the court clarifies case law.
The reading list takes the top 30 judgements per month that reach a minimum of
76 rule points. Lawyers can change weights in `rules.yaml` without touching code;
`suva fit-weights` suggests weights from data.

**Feedback loop.** Reviewers mark judgements as relevant or not. On the *Rule
weights* screen the app refits the weights on Suva's picks plus these verdicts
and shows, before anything changes, how many verdicts the old and new weights
agree with, the recall of Suva's past picks and which judgements would enter or
leave the list. Weights change only when an expert accepts.

**Drafting.** Each draft uses the judgement, a factual brief and up to three
Suva examples in the target language, chosen by word overlap. Apertus returns
numbered source lines as evidence; the app looks those lines up in the ruling
itself, so every quote shown is verbatim. A brief whose quotes cannot be found in
the ruling is rejected and the lawyer is asked to retry. German and French are
written separately, not translated. A glossary of Suva's DE/FR terms
(`rules/glossary.yaml`) is passed to the prompt, and a term from the other
language in a draft is flagged.

**Additional features.** Lawyers can add any judgement by docket or upload; it is
parsed and scored like the others. Approved summaries form a searchable library
with questions answered by Apertus and cited cases. A precedent search finds
similar judgements for a case description (BM25 plus citation overlap, no model).
Optionally, ElevenLabs narrates approved summaries and lets lawyers ask
questions about a judgement by voice.

### Target architecture

The prototype is deployable **on-premise (a)** and in a **sovereign Swiss cloud
(c)**, and **air-gapped (b)** with the steps below. It is a Python process with
SQLite files and a static web build; it binds to localhost and needs no other
service at runtime except the language model.

| Dependency | When | Needed for | Sovereign / air-gapped option |
|---|---|---|---|
| PyPI, npm | build time | Python and frontend packages | Internal package mirror. Fonts are bundled in the build; the page loads nothing from a CDN. |
| Apertus (OpenAI-compatible API) | runtime | Briefs, drafts, questions | Keera/CSCS gateway in Switzerland today. Any self-hosted Apertus behind an OpenAI-compatible server works by changing `LLM_BASE_URL`. |
| Hugging Face (OpenCaseLaw), entscheidsuche.ch | runtime, `fetch` only | New judgements | Run `fetch` on a connected machine and copy `data/` in, or point the fetcher at an internal mirror. Scoring and review then run offline. |
| ElevenLabs | runtime, optional | Narration, voice questions | Off by default when no key is set. Leave it off in a sovereign or air-gapped deployment; the rest of the app is unaffected. |

`LLM_CACHE_MODE=readonly` replays cached pipeline calls without network access.
For Azure (bonus criterion), the app maps to one container in Switzerland North
with Azure Files for `data/`; the storage layer sits behind a repository
interface so SQLite can be replaced by PostgreSQL. We have not built a container
image yet.

## 3. Use of Apertus

- **Model:** Apertus v1.5 70B (`apertus-v1-5-70b`) via the Keera gateway, set
  with `LLM_NAME`, `LLM_BASE_URL` and `LLM_API_KEY`.
- **Tasks:** factual brief, German draft, French draft, questions over a
  judgement and over the library, scripts for audio briefings, and an optional
  relevance judge (`suva judge`) for evaluation.
- **Not used for:** ranking. We tested Apertus as the only relevance judge: it
  rated all 25 of 25 candidates "high", including 7 that Suva did not pick. Rules
  separate them better and explain every rank, so Apertus drafts and the rules
  rank.

**Prompting.** Each prompt asks for a JSON object and treats supplied texts as
data, not instructions. Invalid JSON gets one retry with a stricter instruction.
Style comes only from the 40 few-shot examples; the 15 held-out summaries are
never in a prompt.

**Auditing and privacy.** Pipeline calls are recorded in an append-only audit
table with model, host, prompt version and input/output hashes, never prompt
text or keys. Logs contain IDs and error classes, never judgement text, prompts
or questions. Voice recordings are not stored.

**Cost.** We drafted German and French summaries for the 10 best-ranked
judgements of the current window with a fresh database, so no draft was reused.

| Measure | 10 judgements | Per judgement (DE + FR) |
|---|---|---|
| Apertus calls | 39 | 3.9 |
| Input tokens | 274,338 | 27,434 |
| Output tokens | 17,374 | 1,737 |
| Total tokens | 291,712 | 29,171 |
| Model time | 629 s | 63 s |

94 % of the tokens are input: the ruling, the brief and the style examples. The
minimum is three calls per judgement (brief, German, French); the extra nine are
retries, including one judgement (8C_153/2026) whose brief failed the evidence
check twice and produced no draft. At Suva's pace of about 8 picked judgements a
month, drafting costs roughly 230,000 tokens a month.

## 4. Data

| Data | Source | Use | Notes |
|---|---|---|---|
| 55 bilingual summaries and the spreadsheet of picked judgements | Suva, provided for the challenge (`judgements/`) | Style examples, evaluation labels | Provided by the challenge partner for this event. |
| Federal Supreme Court judgements | OpenCaseLaw snapshot (`voilaj/swiss-caselaw` on Hugging Face) and entscheidsuche.ch | Judgements to rank and summarise | Court decisions are not protected by copyright in Switzerland (Art. 5 URG). Published decisions are anonymised by the court. Raw downloads (about 1 GB) stay in `data/raw/` and are not committed. |

The knowledge base holds 6,499 judgements: 3,936 in the development window
(October 2025 to March 2026, used for tuning) and 2,541 in the current challenge
window. The app stores reviewer names and initials only in the local
`data/review.sqlite`, to sign approvals; there are no accounts or passwords.
Databases, caches, `.env` and generated outputs are excluded by `.gitignore`.
No data comes from human subjects.

## 5. Results

**The task.** In the development window Suva summarised 59 of 3,936 judgements
(1.5 %). All training picks come from the social-insurance divisions, but so do
229 accident and invalidity cases, of which only 47 were picked. Finding the
topic is easy; finding the significant judgements within it is the hard part.

**Ranking, cross-validated on the training split** (October 2025 to February
2026, 47 picks). Weights are refitted on four fifths of the dockets and the
remaining fifth is scored, five times, so no judgement is scored by weights that
saw its label.

| Configuration | Average precision | Picks in top 10 | Picks in top 20 |
|---|---|---|---|
| Topic rules only | 0.56 | 7 | 12 |
| Topic + significance rules | **0.64** | **9** | **14** |

| Reading list: top 30 per month | Recall | Precision | Average precision |
|---|---|---|---|
| Refitted weights (honest) | **87 %** | 33 % | 0.71 |
| Shuffled labels (control) | 14 % | 4 % | 0.06 |
| Random order (control) | 4 % | 1 % | 0.02 |

A lawyer reading 30 judgements a month would have seen about 9 in 10 of the
judgements Suva summarised. Of the remaining 2 in 3 on the list, many are cases
Suva did not summarise but that are on topic; the spreadsheet only labels
positives, so we cannot say how many of them a lawyer would also find relevant.
The March 2026 test split (12 picks) has not been used for any decision and
gives the final number (`suva evaluate --split test`).

**Current window.** Of the 2,541 judgements, 126 are on the reading list
(Relevant), 24 are above the floor but outside the top 30 of their month
(Review), and 2,391 are Low relevance.

**What we tried and removed.** Similarity to past picks, through shared
citations and multilingual embeddings (BAAI/bge-m3, run locally), added +0.03
and +0.01 average precision on top of the significance rules. Among cases on the
same topic, picks and non-picks read alike (embedding similarity 0.82 vs 0.81),
so we removed it to keep the system simpler. An earlier Apertus-based triage
reached 74 % recall on a small holdout, at one or two model calls per judgement;
the rules reach higher recall in cross-validation without any model call.

**Engineering.** About 790 offline tests cover the pipeline, the app and the
Apertus client with test doubles, plus browser tests with WCAG 2.1 A/AA scans
and keyboard checks against fictional data.

## 6. Limitations

- **Summary quality is not yet measured.** We have not scored the drafts against
  the 15 held-out Suva summaries; that is the next evaluation, with Suva's
  lawyers. Drafts always require legal review before approval.
- **Few labels.** 47 training picks: differences of one or two picks in the top
  20 are within noise. Significance signals were chosen by looking at training
  data, so part of the gain may not generalise.
- **One-sided labels.** The spreadsheet lists what Suva summarised, not what it
  rejected. A judgement Suva skipped may still be relevant.
- **Missed invalidity cases.** The last picks found are invalidity cases that no
  significance rule targets yet; reading 90 % of picks still needs about 155
  judgements.
- **Coverage.** The sources show which judgements they listed; they cannot prove
  the court's feed is complete. Some decisions have no known publication date;
  they are included and counted.
- **Evidence is not interpretation.** A quoted source line is verbatim, but the
  model's reading of it can still be wrong.

## 7. Reproducibility

Python 3.11+, Node.js 20.19+ or 22.12+ for the frontend build. Copy
`.env.example` to `.env` and set `LLM_NAME`, `LLM_BASE_URL` and `LLM_API_KEY`.

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -e . pytest
python -m pytest -q                                # offline tests, no network
python -m suva llm-ping                            # one live Apertus call
python -m suva init-db
python -m suva import-supplied                     # Suva's summaries and picks
python -m suva split-examples --manifest data/split-manifest.json
python -m suva fetch --window challenge            # last 3 months; first run downloads ~1 GB
python -m suva parse
python -m suva score
npm --prefix src/frontend ci && npm --prefix src/frontend run build
python src/app.py serve --strict                   # http://127.0.0.1:5050
python src/app.py summarize-top --limit 10         # DE/FR drafts for the 10 best-ranked
```

To reproduce the ranking results: `fetch --window dev`, `parse`, then
`suva dataset`, `suva evaluate`, `suva compare` and `suva cv`. Each number in
`docs/pitch-stats.md` names the command that produced it. `--today YYYY-MM-DD`
on `fetch`, `score` and `app.py` pins the window.

## License

Apache License 2.0 (see `LICENSE`). All HackApertus projects are open source.

## References

- Apertus v1.5: https://huggingface.co/swiss-ai/Apertus-v1.5-70B
- OpenCaseLaw Swiss case-law snapshot: https://huggingface.co/datasets/voilaj/swiss-caselaw
- entscheidsuche.ch: https://entscheidsuche.ch
- Swiss Copyright Act (URG), Art. 5: https://www.fedlex.admin.ch/eli/cc/1993/1798_1798_1798/de
- BAAI/bge-m3: https://huggingface.co/BAAI/bge-m3
