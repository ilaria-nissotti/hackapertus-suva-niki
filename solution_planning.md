# Suva challenge planning skeleton

Use this during planning to record evidence, compare options, and assign open questions. For each step, capture the owner, evidence, decision still needed, and how the team will verify it.

The [challenge brief](challenge_description.md) calls for finding Swiss Federal Supreme Court judgements published in the last three months, explaining and ranking their relevance, drafting German and French summaries in Suva’s style, and making them available for future research. The workshop proposed a mostly deterministic workflow, AI-assisted writing, and a feedback loop. The choices below are open for discussion.

**Owners (fill in):**

| Section | Owner | Decision still needed | Verified by |
|---|---|---|---|
| 1. Relevance & reviewer workflow | | | |
| 2. Source text | | | |
| 3. Triage & flagging | | | |
| 4. DE/FR summaries | | | |
| 5. Review, retrieval, reuse | | | |
| 6. Feedback loop | | | |
| 7. Evaluation & demo | | | |
| 8. Architecture & deployment | | | |

**Two time windows, never mixed:**
- **Dev window (labelled):** 2025 Q4 – 2026 Q1, i.e. the period of the supplied xlsx. Tune rules, prompts and example selection here, then freeze.
- **Challenge window (unlabelled):** the last 3 months of publications before the demo (≈ Sep–Nov 2026). Run the frozen pipeline here. This is what Suva's experts will judge against their hidden hold-out set.
- All source counts in section 2 refer to the challenge window.

## 1. Define relevance and the reviewer workflow

- **Research:** Walk through representative decisions with a legal reviewer. Identify what makes a judgement relevant to accident insurance generally and what makes it relevant to a specific case. Collect clear positives, clear negatives, and borderline examples.
- **Brainstorm:** Define what classification, relevance ranking, and passage flagging should each accomplish. Decide what users provide when they want case-specific matching, and when ambiguous results need review.
- **Planning output:** Initial relevance rubric, sample reviewer tasks, and example judgements with reasons for their labels.
- **Potential solution (research):**
  - *Rubric to pilot with Suva:* Score accident-insurance relevance on a 0–3 scale, from unrelated through incidental, material, and central. Record precedent importance and case-specific fit as separate fields. A prominent precedent, Suva's participation, or an appeal outcome is a signal to examine. Ask reviewers for inclusion or exclusion reasons and an uncertainty flag.
  - *Case-specific matching option:* Compare direct lexical search with query expansion into legal issues and multilingual terms. Use synthetic or cleared queries during development. Show matching and distinguishing factors with source references.
  - *Review queue option:* Sample high-ranked, borderline, and dropped decisions. Include rule/model disagreements and evidence verification failures.

  - *Positive signals to walk through with the reviewer:*
    - Art. 4 ATSG accident definition
    - Art. 6 Abs. 2 UVG list injuries
    - Art. 9 UVG occupational disease
    - status quo sine/ante
    - BGE 115 V 133 / 134 V 109 / 129 V 177 / 134 V 72 / 146 V 51 / 140 V 356
  - *Open questions for Suva reviewers:*
    - Do IV cases that use Suva expert reports count?
    - Do VVG/Haftpflicht cases that touch UVG regress (Art. 72 ATSG) count?
    - Are Nichteintreten decisions ever relevant?
  - *Sources:* [UMBRELA](https://arxiv.org/abs/2406.06519), [Suva Medical 2025 Unfall/Krankheit](https://www.suva.ch/de-ch/unfall/fuer-leistungserbringer/suva-medical/publikationen/2025/juni/abgrenzung-zwischen-unfall-uvg-und-krankheit-kvg), [Thomas et al. LLM relevance labels](https://arxiv.org/abs/2309.10621)

## 2. Collect and prepare the source text

- **Research:** Inspect how the source exposes publication dates, judgement identifiers, languages, document text, and updates. Check access constraints and what the supplied summaries actually cover.
- **Brainstorm:** How will we verify coverage of the date window, handle duplicates and extraction failures, and link each extracted passage back to the original judgement?
- **Planning output:** Source inventory, representative inputs, and an outline of the metadata and provenance needed downstream.
- **Potential solution (research):**
  - *Supplied data (inspected 2026-11-27):*
    - `judgements/Export_*.html` (55 files) are **Suva's human summaries, not judgement texts**. Each has:
      - a title in the decision's language ("Arrêt du TF …" ×31, "BGer-Urteil …" ×22, "Sentenza …" ×2)
      - "Text DE", "Text FR", a bger.ch link and the author
    - **No full judgement texts exist locally.** Every judgement must be fetched. The bger.ch link in each export gives the summary→judgement mapping for free (55 judgement/summary pairs).
    - `judgements/BG_Urteile_2025_Q4_2026_Q1.xlsx` has 60 rows: quarter, docket, "Erstellt" and a SharePoint link. **There is no summary text.**
    - "Erstellt" is when Suva wrote the summary, not when the court published the decision. Map each docket to its publication date before using it as a dev-window label.
    - Overlap: 31 dockets appear in both files, 29 only in the xlsx (no example text), and 24 exports are older cases not in the xlsx. That gives ≈84 distinct positives, 55 of them with text.
    - Normalise two malformed xlsx rows: "BGer-Urteil 458/2025 vom 18. November 2025" is missing its prefix (probably 8C_458/2025, which is among the exports), and "Entscheid des Kantonsgerichts Basel-Landschaft vom 5. Februar 2026" is a **cantonal** decision, outside bger.ch.
    - Not every positive is 8C/9C: `4A_106/2025` (civil law) and the cantonal case show that Suva also summarises decisions outside the social-insurance chambers.
    - `judgements/read.me` and `test.txt` are empty.
  - *Source options:* Compare an official feed requested through Suva, approved mirrors such as entscheidsuche and OpenCaseLaw, and manual imports. Choose a primary and fallback after checking access, licensing, freshness, and coverage. Treat overlapping mirrors as potentially sharing omissions.
  - *Coverage ledger and replay option:* Record docket, source, retrieval time, publication-date provenance, and status (discovered, fetched, parsed, failed, or missing from an expected source). Reconcile against an official listing or independently assembled reference sample where available. Freeze a corpus snapshot for scoring comparisons.

  - *Exploratory source counts (recorded 2026-11-27, completeness unverified):*
    - 1,295 BGer decisions since 2026-08-27, 136 of them in chamber 8C.
    - A Suva/UVG/LAA full-text query on 8C returned 54 hits.
  - *Date window:* Use verified publication dates for the challenge window. Store decision date and first-seen date separately. Where publication date is unknown, mark it unknown and report coverage for those records separately.
  - *Metadata:*
    - Derive the chamber from the docket prefix and the decision's own header. 8C has been the IV. öffentlich-rechtliche Abteilung since 2023, and entscheidsuche mislabels it.
    - Don't trust `docs/Index/CH_BGer/last` (it was stale). List the directory instead.
  - *Parsing:*
    - Decisions are a flat `div.para` sequence, with `a.bgeref_id` for BGE links and `artref` for statute links.
    - Segment with regex on Sachverhalt/Faits, Erwägungen/Considérant en droit, numbered `^\d+(\.\d+)*` and Demnach erkennt / Par ces motifs.
    - Chunk ID format: `8C_447/2026#E.3.2`.
  - *Dedupe and failures:*
    - Key on docket plus decision date. One docket can carry several decisions.
    - Flag access-denied or challenge pages, very short texts, a missing docket, or a missing Dispositiv as fetch or parse failures.
    - The `Export_*.html` files are summaries and need their own small parser (title, Text DE, Text FR, link, author). Do not run them through the judgement parser.
  - *Risks:* bger.ch robots.txt and terms of use are unverified (the request was blocked). entscheidsuche's licence was not checked. The RSS and Neuheiten fields are unverified.
  - *Sources:* [entscheidsuche API PDF](https://entscheidsuche.ch/pdf/EntscheidsucheAPI.pdf), [OpenCaseLaw repo](https://github.com/jonashertner/caselaw-repo-1), [opencaselaw bger.py](https://github.com/jonashertner/caselaw-repo-1/blob/main/scrapers/bger.py), [chamber runbook](https://github.com/jonashertner/caselaw-repo-1/blob/main/runbooks/bger_chamber_header_first_2026-11-06.md), [NeueScraper](https://github.com/entscheidsuche/NeueScraper), [HF voilaj/swiss-caselaw](https://huggingface.co/datasets/voilaj/swiss-caselaw)

## 3. Compare approaches to classification, triage, and flagging

- **Research:** Test keyword and phrase rules on sample judgements. Include terminology variants, incidental mentions of Suva, negation, and cases where the relevant legal issue appears only in the reasoning. Check whether enough labeled examples exist to evaluate a traditional text classifier.
- **Brainstorm:** Compare keyword rules, weighted scoring, a lightweight classifier, and combinations. Decide how scores should be interpreted, how conflicting signals should be handled, and what evidence supports both inclusion and exclusion.
- **Planning output:** Candidate baselines, example explanations and flagged passages, and a test for when extra complexity earns its place.
- **Potential solution (research):**
  - *Compare on the same frozen corpus:* Start with section-aware rules, then test lexical retrieval and borderline-only Apertus review as separate additions. Compare that cascade with Apertus grading every candidate if time permits. Consider embeddings or a learned score when error analysis identifies a gap such as unfamiliar wording.
  - *Candidate coverage:* Treat chamber, insurer identity, and statute references as features. **Do not hard-gate on the 8C/9C docket prefix**: known positives include `4A_106/2025` (civil law) and a cantonal decision. The recall stage needs a full-text signal (Suva/UVG/LAA lexicon) across all chambers. Sample outside 8C/9C and inspect rule-rejected cases. Measure recall before ranking and record why each item was included or excluded.
  - *Evidence output option:* For each flagged issue, show the source passage and consideration number, who made the statement, and whether the court resolved the issue. Reuse the evidence in ranking review and both language drafts.

  - *Lexicon, DE/FR/IT:*
    - UVG/LAA/LAINF, Suva/CNA/INSAI
    - Unfallbegriff, natürliche/adäquate Kausalität / causalité naturelle/adéquate
    - status quo sine/ante, Listenverletzung, Integritätsentschädigung, Berufskrankheit
    - the BGE numbers listed in section 1
    - Weight Erwägungen hits 2–3× higher than Sachverhalt hits. SCALE shows considerations beat facts, e.g. 84.9 vs 76.9 macro-F1. *(The weight itself is an inference.)*
  - *Negation and incidental mentions to down-weight (unverified):*
    - Suva only in the party list or as Beigeladene
    - "kann offenbleiben", "nicht Gegenstand", "n'a pas à être examiné"
    - UVG mentioned only in the facts
  - *Explanations:*
    - Require verbatim quotes with E. numbers.
    - Fuzzy-match each quote against the source. Drop failed quotes and flag the item.
    - Highlight the "Streitig ist / Est litigieux" sentence.
  - *Sortable score:* The 0–3 rubric alone produces many ties, so the ranking needs a continuous score, e.g. `score = w1·rule features + w2·similarity to nearest known positives + w3·Apertus rubric (0–3)`.
    - Fit w1–w3 with a small logistic regression on dev-window positives vs. not-selected decisions (weak negatives).
    - Break remaining ties by rule score, then publication date.
  - *Calibration:*
    - Optionally compare repeated model grades with single-pass grading. Measure whether disagreement predicts reviewer corrections before using it to prioritize review.
    - Use logprobs only if the gateway exposes them (unknown).
  - *Classifier baseline (when extra complexity earns its place):*
    - Logistic regression on lexicon features plus BGE-M3 embeddings, or SetFit.
    - Compare candidate recall, expert-confirmed top-30 relevance, and reviewer effort on the same development cases. Reserve the final holdout for the frozen pipeline.
  - *Models:* `swiss-ai/Apertus-v1.5-70B` / `-8B` (the 8B as a cheap inclusive first pass), `BAAI/bge-m3`, `BAAI/bge-reranker-v2-m3`.
  - *Risks:*
    - LLM judgements are sensitive to prompt paraphrase, so select the prompt against gold labels.
    - Apertus scores LEXam-Hard 24.44, so keep it rubric-bound.
    - No study of Apertus on Swiss relevance triage exists.
  - *Sources:* [SCALE / One Law Many Languages](https://arxiv.org/html/2306.09237v3), [Thomas et al.](https://arxiv.org/abs/2309.10621), [UMBRELA](https://arxiv.org/abs/2406.06519), [COLIEE BM25 baseline](https://arxiv.org/abs/2105.05686), [DoSSIER hybrid](https://arxiv.org/abs/2108.03937), [SetFit](https://arxiv.org/abs/2209.11055), [bge-reranker-v2-m3](https://huggingface.co/BAAI/bge-reranker-v2-m3)

## 4. Draft and verify German and French summaries

- **Research:** Compare the supplied German and French examples for structure, terminology, level of detail, and treatment of facts, reasoning, and outcome. Check the challenge’s Apertus requirements and available access.
- **Brainstorm:** Decide what source material Apertus needs, whether each language draft should use its own examples, and how reviewers will trace claims back to the judgement. Explore how to catch omitted qualifications, unsupported claims, and terminology drift.
- **Planning output:** Draft acceptance criteria and a small bilingual evaluation set.
- **Potential solution (research):**
  - *Recommended approach (design inference, not benchmarked on Apertus):*
    1. Segment the judgement deterministically.
    2. Extract a fact sheet as JSON at T=0: Suva's role, instance below, legal questions, holding per question with Art. and E. numbers, qualifiers, outcome, costs.
    3. Generate each language from the fact sheet plus source excerpts, using the **2–4 nearest Suva examples in that language** as few-shot turns.
    4. Validate the output format and retry on failure.
  - *Match the real Suva style (from the 55 exports):*
    - No section headings. The output is **compact prose like a headnote**, stating the legal rule and the court's reasoning. Median length is ≈180 words for DE and ≈225 for FR (range 8–670).
    - Output format matches the export:
      - title in the decision's language ("BGer-Urteil 8C_x/2025 vom …" / "Arrêt du TF 8C_x/2025 du …" / "Sentenza …")
      - Text DE, Text FR
      - bger.ch link
      - an author field left for the reviewer
    - Validate length (within the observed range) and that no headings or bullet points are present.
  - *Source language and DE/FR relationship:*
    - The examples' DE and FR texts look closely parallel. Check several pairs. If they are, the decision's language is likely the master:
      - write that language first from the judgement;
      - then *adapt* (not literally translate) the other one using the glossary and same-language examples.
    - If the pairs differ in content, generate each language independently from the source.
    - SwiLTra-Bench found headnotes hard to translate even with specialised MT, so either way, check terminology and do not use raw MT.
  - *Italian decisions:* 2 of the 55 examples come from Italian judgements ("Sentenza"). The pipeline must accept an IT source and produce both DE and FR. Add IT terms to the lexicon and glossary.
  - *Author style:* There are 3 authors (one wrote 38 of the 55). Prefer few-shot examples from a single consistent author, or test whether mixing authors hurts style scores.
  - *Glossary:*
    - Build DE↔FR term pairs from TERMDAT and parallel BGE Regesten, e.g. Integritätsentschädigung↔indemnité pour atteinte à l'intégrité, UVG↔LAA, ATSG↔LPGA, abgewiesen↔rejeté.
    - Inject only the terms found in the source, then post-check the FR output for each one.
  - *Verification, cheapest first:*
    - Every Art. and BGE reference in the summary must exist in the source.
    - Every E./consid. number must exist in the consideration index.
    - Verify each disposition item that affects the summary, including partial decisions, remands, and costs where relevant.
    - Amounts and percentages must match.
    - Per-claim `[E. x.y]` tags are checked by an Apertus judge (entailed / contradicted / not found).
    - Verify attribution and legal status. Distinguish the court’s holding from party submissions, lower court findings, and questions left open.
    - Hedges must be preserved (grundsätzlich, offen gelassen / laissé ouvert, sous réserve).
    - Run a claim-by-claim DE↔FR consistency check.
  - *Settings:*
    - T 0–0.3 for the final text.
    - Thinking mode is optional for analysis. It needs a large `max_tokens`, the reasoning block must be stripped, and it has no tool calls.
    - The model card states 262,144 tokens of context, but hosted gateways often cap context and output length. **Measure the real limits on the CSCS/Keera gateway first.** Fall back to sending only the segmented Erwägungen plus Dispositiv if needed.
    - A/B test 8B vs 70B on the template. IFBench is 40 for the 8B vs 30 for the 70B, both without thinking.
  - *Acceptance criteria (draft):*
    - 100% of deterministic checks pass.
    - No unsupported claims.
    - Template sections present.
    - Glossary compliance.
    - SLDS five-criterion rubric plus house style ≥ an agreed bar.
    - Reserve evaluation summaries and related judgements before selecting few-shot examples. Use only the development pool for examples and prompt tuning.
  - *Open questions:*
    - Exact model rules on the [Notion page](https://hackapertus.notion.site/zurich-challenges-model-requirements) (the JS page could not be read).
    - Are non-Apertus judges and embedders allowed?
    - Keera gateway URL, model IDs and JSON-mode support.
  - *Release date:* The notes disagree on when Apertus v1.5 was released. Don't cite a date until a primary source confirms it.
  - *Sources:* [Apertus v1.5 8B card](https://huggingface.co/swiss-ai/Apertus-v1.5-8B), [70B card](https://huggingface.co/swiss-ai/Apertus-v1.5-70B), [CSCS Inference API](https://docs.cscs.ch/services/inference/api/), [SLDS](https://arxiv.org/html/2410.13456v3), [SwiLTra-Bench](https://arxiv.org/html/2503.01372v2), [TERMDAT](https://www.bk.admin.ch/bk/en/home/dokumentation/sprachen/termdat.html), [LeMAJ](https://arxiv.org/abs/2510.07243)

## 5. Plan review, retrieval, and reuse

- **Research:** Find out how reviewers currently inspect, approve, and retrieve summaries, and what they need when submitting an additional judgement.
- **Brainstorm:** Sketch how the reviewer sees the original judgement, sortable relevance score, triage explanation, flagged evidence, and both summaries. Decide what becomes searchable, how corrected versions are handled, and how users find prior work.
- **Planning output:** Reviewer walkthrough and a list of search and review tasks to support in the demo.
- **Potential solution (research):**
  - *Recommended approach:* Streamlit, which is already pinned at `>=1.38`.
    - **Triage page:** `st.dataframe(on_select="rerun", selection_mode="single-row")` with `column_config` (ProgressColumn for the score, LinkColumn for the bger.ch link). Columns: docket, date, chamber, score, label, one-line reason, status. Sidebar filters.
    - **Detail view:** Left, the source judgement with `<mark>`-highlighted evidence and rule tooltips. Right, `st.tabs(["DE","FR"])` with the editable summary, explanation and score breakdown.
    - **Approve form:** relevance and score override, passages added or removed, edited text, comment.
    - **"Summarise additional judgement":** a docket, URL or upload field.
    - **Search/Ask:** a page for searching and asking questions over the KB.
  - *Versions:* Summaries are append-only, with `parent_id` and a `status` of draft or approved. Show a `difflib` diff between the draft and the edit. Only approved summaries become searchable as "approved".
  - *Search:* hybrid FTS5 and vector search over passages and approved summaries. RAG answers cite stable IDs (`[8C_129/2025 E.4.2]`), and cited IDs are validated against the retrieved set.
  - *Risks:* No auth roles in open-source Streamlit. Use a reviewer-name field for the demo and Entra ID / Container Apps auth on Azure (unverified). Keep the logic in `src/` (no Streamlit imports) so a FastAPI layer can be added later.
  - *Sources:* [st.dataframe](https://docs.streamlit.io/develop/api-reference/data/st.dataframe), [row selection tutorial](https://docs.streamlit.io/develop/tutorials/elements/dataframe-row-selections), [SQLite hybrid search](https://simonwillison.net/2024/Oct/4/hybrid-full-text-search-and-vector-search-with-sqlite/)

## 6. Design the feedback loop

- **Research:** Identify which corrections reviewers can reliably make and who can validate disagreements. Look for examples of systematic misses and one-off mistakes.
- **Brainstorm:** Decide which feedback to capture across relevance, ranking, passages, and summary edits. Discuss what should trigger a rule change, a prompt or example update, or classifier retraining if a classifier is used. Decide how to version changes and evaluate them on examples held out from tuning.
- **Planning output:** Feedback fields, review responsibilities, and a walkthrough from one correction to a verified improvement.
- **Potential solution (research):**
  - *Feedback fields:*
    - `judgement_id`, `target` (triage / passage / summary_de / summary_fr), `run_id`
    - `model`, `prompt_version`, `rules_version`
    - `before`, `after`, `diff`
    - `label_override`, `score_override`, `passages_added[]`, `passages_removed[]`
    - `error_type` enum: missed_relevant, false_positive, wrong_outcome, omitted_qualification, unsupported_claim, terminology, style, translation_not_adapted
    - `comment`, `reviewer`, `created_at`
  - *Correction → action mapping (inference):*
    - Missed concept → add to the lexicon or rubric.
    - Incidental mention → add a negation pattern or clarify the rubric.
    - Recurring error type → add a new few-shot example.
    - Enough adjudicated corrections to test an observed gap → compare a retrained classifier with the current baseline, if a classifier is used.
  - *Versioning:* Keep `prompts/*.yaml` and `rules/keywords.yaml` in git. Each run records `git rev-parse HEAD` plus YAML hashes. Promote a change only after it is re-evaluated on the held-out split.
  - *Reviewer sampling option:* Mix top-ranked decisions, borderline cases, disagreements, and randomly selected rejected items. Record the sampling reason and adjudicate disagreements before using corrections as labels.
  - *Walkthrough for the demo:*
    1. A reviewer marks a false negative with `missed_relevant` and a reason.
    2. The term is added to the lexicon YAML.
    3. Replay the correction as a regression test and compare the updated pipeline on untouched examples. Report the observed result, including any regressions.
    4. The new version is tagged.
  - *Risks:* No sourced case studies of legal-AI feedback loops. Who resolves reviewer disagreements is a team decision.
  - *Sources:* [Thomas et al. (prompt selection against gold)](https://arxiv.org/abs/2309.10621), [SetFit](https://arxiv.org/abs/2209.11055)

## 7. Set evaluation criteria and demo scope

- **Research:** Check the cost of missing relevant decisions versus surfacing too many. Review the repo’s sovereign deployment requirements and identify external dependencies during development and at runtime.
- **Brainstorm:** Choose measures for source coverage, triage accuracy, ranking usefulness, bilingual summary fidelity, and reviewer effort. Set targets after examining sample data. Agree on what can be demonstrated end to end and what evidence would justify further work.
- **Planning output:** Evaluation plan, demo sequence, and unresolved decisions with owners.
- **Potential solution (research):**
  - *Triage metrics:*
    - Report known-positive coverage from source discovery through ingestion, candidate generation, and top-30 ranking. Label recall on this set as known-positive recall.
    - For the challenge top 30, report the expert-confirmed relevant count and how many remain unjudged. Pool outputs from candidate systems for review, then sample lower-ranked and dropped items to look for misses outside the pool.
    - Treat unsummarised decisions as unlabeled. Ask reviewers to label a small stratified sample with hard negatives, borderline decisions, and some cases outside 8C/9C.
    - Optionally test on a later, unseen publication batch after freezing rules, prompts, and example selection. Keep related decisions together across splits and report sample sizes alongside scores.

  - *Summary metrics:*
    - Deterministic check pass rate and claim-support rate.
    - SLDS rubric (accuracy, completeness, clarity, articles, considerations, each 1–3) plus house style.
    - ROUGE-L, BERTScore and chrF only for regression tracking. In SLDS, experts correlated only about 0.26 with ROUGE-L or an LLM judge.
  - *Top-10 evaluation:* Select the 10 highly relevant judgements using the relevance ranking. Report summary fidelity separately, including drafts that fail verification. Document any substitutions in the demo.
  - *Coverage:* Compare docket sets and publication lag across sources, using an independent reference sample where available. Report unresolved gaps and unknown publication dates.
  - *Reviewer effort:* time per approval and edit distance between draft and approved text.
  - *LLM call budget:*
    - The full pipeline makes roughly 10–30 calls per decision: borderline triage, fact sheet, DE, FR, and claim-by-claim checks. Over ~1,300 decisions in the window, that will hit shared-gateway rate limits.
    - Mitigations:
      - Cache every call, keyed by a hash of model + prompt version + input.
      - Run the full summary pipeline only on the top-N (e.g. top 30). Lower ranks get triage only.
      - Use resumable batch jobs with retry/backoff.
      - Measure the gateway's rate limit early.
  - *Demo robustness:*
    - Judges must be able to run it end to end, so the **default is a frozen snapshot mode**: the corpus and precomputed triage and summaries ship with the repo or a download script.
    - Live mode (fetch and regenerate) is optional, behind a flag. A blocked scraper or a gateway outage must not break the demo.
  - *External dependencies:*
    - Build time: pip wheels, gated Apertus weights, embedding weights, vLLM image, mirror download.
    - Runtime depends on mode. For connected operation, document the approved publication source and inference endpoint. For air-gapped operation, test importing a dated corpus snapshot and processing additional judgements from local text with local inference. Verify behaviour with outbound access disabled.
  - *Demo sequence:*
    1. Ingest the window.
    2. Open the sortable triage table.
    3. Open the top item with highlighted evidence.
    4. Show the DE/FR summaries with check badges.
    5. Approve with an edit.
    6. Search the KB.
    7. Summarise an additional docket.
    8. Show the feedback → eval improvement.
    9. Optionally demonstrate the offline snapshot and local inference path if deployment is part of the demo.
  - *Sources:* [SCALE metrics](https://arxiv.org/html/2306.09237v3), [SLDS evaluation](https://arxiv.org/html/2410.13456v3), [LLM-ReSum metric meta-eval](https://arxiv.org/html/2604.25665v1)

## 8. Architecture & deployment (research)

The implementation details below are deployment options. Before adopting a gateway, embedding model, or auxiliary judge, verify that it is allowed and available in the chosen deployment mode.

- **Knowledge base:**
  - One SQLite file: FTS5 (BM25, `unicode61 remove_diacritics 2`) plus sqlite-vec, fused with Reciprocal Rank Fusion in one SQL query.
  - Tables: `judgements`, `passages` (with E. ref and char offsets), `triage`, `summaries` (versioned), `feedback`, `audit_log`.
  - Hide it behind a repository interface (`DATABASE_URL`) so it can move to PostgreSQL + pgvector/DiskANN.
  - Embeddings: `BAAI/bge-m3` (MIT, 8,192 tokens, dense+sparse) or `Qwen/Qwen3-Embedding-0.6B` (Apache-2.0). Bake the weights into the image. Use an FTS-only fallback flag if the download is a risk.
- **UI:** Streamlit, as in section 5. Pipeline logic lives in `src/` so a FastAPI layer can be added later.
- **Apertus serving:**
  - Hackathon: the CSCS gateway, `https://api.inference.cscs.ch/v1` (OpenAI-compatible; it also has `/v1/embeddings` and `-thinking` variants). The Keera URL still needs to come from the organisers.
  - On-prem: image `ghcr.io/swiss-ai/vllm_apertus_1.5_release:latest-amd64`, started with `vllm serve swiss-ai/Apertus-v1.5-8B --max-model-len 262144 --enable-auto-tool-choice --tool-call-parser apertus`. The 70B adds `--tensor-parallel-size 4`.
  - Lower `--max-model-len`, e.g. 32768, on a single small GPU *(inference)*.
  - v1.5 is not yet in upstream vLLM or Transformers. The weights are gated.
- **OpenAI-compatible config switching:**
  - One `openai` client, configured by `LLM_NAME` / `LLM_BASE_URL` / `LLM_API_KEY` (already in `.env.example`).
  - Gateway → `http://llm:8000/v1` (compose vLLM, key `EMPTY`) → an Azure endpoint, all by env vars only.
  - An optional LiteLLM proxy can front Azure. Don't hard-depend on it.
  - Record the model and base-URL host per run.
- **Docker Compose:**
  - `app` (Streamlit + `./data` volume)
  - `llm` under `profiles: [local-gpu]`
  - `db` (postgres+pgvector) under `profiles: [postgres]`
  - Judges run `docker compose up app`.
- **Azure portability:**
  - App → Container Apps. KB → Azure Database for PostgreSQL Flexible Server + pgvector (allow-list via `azure.extensions`, and pin the version: a crash with vector 0.8.0 has been reported). Secrets → Key Vault. Logs → Log Analytics.
  - Azure AI Search would need its own adapter.
  - **Weak spot:** LLM hosting in Switzerland North.
    - Container Apps serverless GPUs: Switzerland North was absent from the Nov 2025 list.
    - Azure OpenAI GPT: per a Q&A thread, Standard deployments are not available there, and Data Zone doesn't guarantee Swiss-only processing.
    - Foundry managed compute for HF models is in preview, and whether Apertus is in that catalogue is unconfirmed.
    - Realistic option: keep calling CSCS/vLLM from Azure, or self-host vLLM on NC-series GPUs where quota exists.
  - Not verified: pgvector in Switzerland North (run `SHOW azure.extensions`) and non-GPU Container Apps there.
- **Sovereignty and compliance:**
  - Verify the chosen deployment against the challenge criteria. Document where inference, storage, logs, backups, and model weights reside. A hosted endpoint requires network access. Record which inputs and outputs can leave the deployment boundary, including case queries, feedback, and logs.
  - The FADP applies directly to AI processing (FDPIC). Switzerland signed the CoE AI Convention, and draft implementing law is expected around end-2026 to 2027.
  - Build:
    - an append-only audit log of LLM calls (model, host, prompt version, input/output hashes) and of reviewer actions
    - "AI draft, not reviewed" labels until approval
    - evidence for every decision
    - no Suva client case data sent to non-Swiss endpoints
    - for confidential queries, a local OpenCaseLaw copy, not the hosted MCP server
  - EU AI Act applicability is treated as good practice only *(inference)*.

## 9. First experiment: coverage and labels audit

1. Normalise the xlsx dockets and extract the bger.ch link from each export. Then match all ≈84 known positives against each candidate source. Record missing dockets, duplicates, parse failures, date discrepancies, and publication lag.
2. Ask a reviewer to label a small sample of unselected and rule-rejected decisions. Include different languages, borderline cases, and a sample outside 8C/9C. Record why the decisions matter or were excluded.
3. Freeze a snapshot and compare the simplest rules baseline against the optional triage approaches in section 3. Use the same labels and report how many known relevant decisions disappear at each stage.
4. Use the results to pick the next investigation. Source gaps point to feed work, reviewer disagreement points to rubric work, and recurring errors among retrieved decisions point to scoring or evidence extraction.

## 10. Live scraping and deterministic pre-filter (team brainstorm)

### Scraping: two needs, two routes

| Need | Requirement | Route |
|---|---|---|
| A. Dev/training data | One-time bulk pull for the dev window; completeness matters more than speed | Merge entscheidsuche + OpenCaseLaw. Use the bger.ch links in the 55 exports. Fill the remaining gaps by hand or with the scraper. |
| B. Live pipeline | Daily, low volume, reliable | bger.ch "new publications" page via Playwright, at a polite rate |

- **Measure first:** run the coverage check (section 9, step 1) before deciding how much scraper effort is needed.
- **Scraping bger.ch directly:**
  - The SHA-256 proof-of-work (`pow.php`) is a rate limit that every browser solves itself. Reproducing it in Python is legitimate; `jonashertner/caselaw-repo-1` already does this. Inspect the requests in the browser's network tab.
  - For the Imperva protection, drive a real browser (Playwright) at human speed:
    - open the daily new-publications list (`index_aza.php?…&mode=news`);
    - fetch all decision pages, not just 8C/9C (see section 3: no docket gate);
    - wait ≈2 s between requests.
    - Volume is a few dozen decisions per day.
  - **Red line:** no rotating proxies, no faked browser fingerprints, no getting around blocks. If we get blocked at a polite rate, switch to the fallback.
- **Live architecture:**
  - Primary source: bger.ch via Playwright.
  - Fallback: entscheidsuche / OpenCaseLaw.
  - A nightly **reconciliation job** compares decision numbers across sources and reports any decision one source has and another lacks. The feed then monitors itself.
- **Production roadmap:** Suva requests an official data feed from the Federal Supreme Court. This also answers "what if the scraper breaks?"
- *Risk:* bger.ch's robots.txt and terms of use are still unverified. Check them before running the scraper at scale.

### Deterministic pre-filter tuned on the dev window
The dev window gives ≈60 relevant cases plus every not-selected decision from the same period (weak negatives, a positive-unlabeled setup). That is enough to tune a rule-based score, but not to train a model.

- **Three zones, not a yes/no decision:**

| Rule score | Action |
|---|---|
| High (e.g. Suva is a party + UVG articles cited in the Erwägungen) | Straight to the shortlist |
| Middle (some signals, nothing conclusive) | Apertus reads it, scores it 0–3 and explains |
| Low (no accident-insurance signals at all) | Dropped, with the reason logged (shown in the UI as "why not relevant") |

- **Tune for recall:** set the "drop" threshold so that (nearly) all known positives stay above it, even if some irrelevant cases get through. The middle zone and Apertus remove those.
- **Example point system** *(illustrative only; derive the real rules and weights from contrastive counting, below)*:
  - +5 Suva is a party
  - +3 UVG/LAA article cited in the Erwägungen (not just the facts)
  - +2 docket prefix 8C (a feature, not a gate)
  - +2 core topic present (causation, integrity compensation, Art. 6 Abs. 2 UVG, …)
  - −3 Suva or UVG only mentioned in passing ("kann offenbleiben", "nicht Gegenstand")
- **Contrastive pattern mining:**
  1. Show an LLM positives *next to* not-selected decisions from the same window and ask what separates them. Ask for features we can check.
  2. Keep a hypothesis only if counting confirms it ("X% of positives vs. Y% of not-selected").
  3. Check with the organisers whether a non-Apertus LLM may be used for this offline analysis *(unverified)*. Runtime scoring stays on Apertus.
- **Avoid fooling ourselves:**
  - Tune on ≈45 positives and their not-selected neighbours; hide ≈15 positives.
  - At the end, check whether the hidden 15 still pass. That is the honest recall number.
  - "Not selected" ≠ "irrelevant": Suva may have skipped routine relevant cases (see redundancy detection in section 11).

## 11. Optional ideas (not yet researched in depth)

- **Use the exports as a paired dataset.**
  - Each export contains its bger.ch link. That gives 55 judgement/summary pairs for few-shot retrieval and summary evaluation, with no searching needed.
  - Split them into a few-shot pool and an evaluation set before any prompt tuning.
- **Court importance signals as features:**
  - decision intended for BGE publication;
  - five-judge panel (Fünferbesetzung);
  - phrases such as "Praxisänderung" / "changement de jurisprudence" or "präzisiert die Rechtsprechung".

  These are the court's own markers that a decision matters. *(Hypothesis; test by counting in the dev window.)*
- **Citation graph:**
  - OpenCaseLaw ships one. Score up decisions that cite the core UVG leading decisions (section 1).
  - In the UI, show "related precedents": older Suva summaries that cite or are cited by the new decision.
- **Redundancy detection:**
  - Cluster new decisions by legal issue and down-rank near-duplicates of already-summarised ones. Show "similar to 8C_x/2025, already summarised".
  - This likely explains part of the "not selected" set and reduces reviewer load.
- **Saved searches and weekly digest** (bonus customer experience): lawyers subscribe to topics (e.g. "adequate causation, psychological consequences") and receive new ranked hits with draft DE/FR summaries.
- **Export in Suva's format:** produce exactly the export layout (title in the decision's language, Text DE, Text FR, link, author), so approved drafts drop straight into the existing SharePoint workflow.
- **Labelling sprint UI:**
  - A 5-minute Streamlit page where Suva's experts at the hackathon mark borderline and rule-rejected decisions relevant / not relevant / unsure, with a one-line reason.
  - This is the fastest way to get real negatives, and it seeds the feedback loop (section 6).

## Research sources

- Data:
  - [entscheidsuche API PDF](https://entscheidsuche.ch/pdf/EntscheidsucheAPI.pdf)
  - [entscheidsuche _search.php](https://entscheidsuche.ch/_search.php)
  - [OpenCaseLaw repo](https://github.com/jonashertner/caselaw-repo-1)
  - [HF voilaj/swiss-caselaw](https://huggingface.co/datasets/voilaj/swiss-caselaw)
  - [NeueScraper](https://github.com/entscheidsuche/NeueScraper)
- Apertus:
  - [v1.5 8B card](https://huggingface.co/swiss-ai/Apertus-v1.5-8B)
  - [v1.5 70B card](https://huggingface.co/swiss-ai/Apertus-v1.5-70B)
  - [CSCS Inference API](https://docs.cscs.ch/services/inference/api/)
  - [HackApertus HF org](https://huggingface.co/HackApertus)
- Swiss legal NLP:
  - [SCALE / One Law Many Languages](https://arxiv.org/html/2306.09237v3)
  - [SLDS summarisation](https://arxiv.org/html/2410.13456v3)
  - [SwiLTra-Bench](https://arxiv.org/html/2503.01372v2)
  - [TERMDAT](https://www.bk.admin.ch/bk/en/home/dokumentation/sprachen/termdat.html)
- Relevance and IR:
  - [UMBRELA](https://arxiv.org/abs/2406.06519)
  - [Thomas et al.](https://arxiv.org/abs/2309.10621)
  - [BGE-M3](https://huggingface.co/BAAI/bge-m3)
  - [bge-reranker-v2-m3](https://huggingface.co/BAAI/bge-reranker-v2-m3)
  - [SetFit](https://arxiv.org/abs/2209.11055)
- Domain: [Suva Medical 2025 Unfall vs Krankheit](https://www.suva.ch/de-ch/unfall/fuer-leistungserbringer/suva-medical/publikationen/2025/juni/abgrenzung-zwischen-unfall-uvg-und-krankheit-kvg)
- Stack:
  - [SQLite hybrid search (Willison)](https://simonwillison.net/2024/Oct/4/hybrid-full-text-search-and-vector-search-with-sqlite/)
  - [Streamlit st.dataframe](https://docs.streamlit.io/develop/api-reference/data/st.dataframe)
  - [LiteLLM configs](https://docs.litellm.ai/docs/proxy/configs)
- Azure:
  - [pgvector on Azure PostgreSQL](https://learn.microsoft.com/en-us/azure/postgresql/extensions/how-to-optimize-performance-pgvector)
  - [Container Apps serverless GPU regions](https://learn.microsoft.com/en-us/azure/container-apps/gpu-serverless-overview)
  - [Azure OpenAI Switzerland North Q&A](https://learn.microsoft.com/en-au/answers/questions/5933839/clarification-needed-gpt-standard-deployments-in-s)
  - [Foundry managed compute](https://learn.microsoft.com/lb-lu/azure/foundry/concepts/managed-compute-overview)
- Compliance:
  - [FDPIC: FADP applies to AI](https://edoeb.admin.ch/en/update-current-legislation-directly-applicable-ai)
  - [BAKOM AI](https://www.bakom.admin.ch/en/artificial-intelligence)
