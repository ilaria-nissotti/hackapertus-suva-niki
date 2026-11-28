"""Draft held-out judgements and compare the drafts with Suva's own summaries (`suva eval-summaries`).

Only the judgement text, its factual brief and few-shot style examples reach the model, exactly
as in the review app. Suva's summary of an evaluated judgement is read only by the metrics here,
so the evaluation split stays unseen by prompts. The metrics are deterministic and
style-oriented; they do not replace a legal review of the drafts:

  paragraphs       number of paragraphs in the draft and in Suva's summary
  length_ratio     draft characters / Suva characters
  opening_overlap  content-word F1 of the first paragraph (Suva opens with the legal finding)
  content_f1       content-word F1 of the whole summary
  statute_recall   share of Suva's statute citations (Art. x UVG, canonical) that the draft cites
  bge_recall       share of Suva's BGE / ATF references that the draft cites
  glossary_recall  share of Suva's glossary terms (rules/glossary.yaml) that the draft uses
  glossary_warnings  other-language terms in the draft
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import logging
import re
from statistics import mean
from typing import Any, Callable

from suva import glossary
from suva.parse.citations import extract

log = logging.getLogger(__name__)
LANGUAGES = ("de", "fr")
STOPWORDS = {
    "de": set("der die das den dem des ein eine einer eines einem einen und oder ist sind war wurde wird werden "
              "nicht sich zum zur vom beim auf aus bei mit nach von vor für über unter wegen dass als auch es "
              "er sie ihr ihre sein seine diese dieser dieses kann hat haben im in an zu so wie nur noch".split()),
    "fr": set("le la les du des un une au aux et ou est sont était ont été être dans par pour avec sur selon "
              "sans que qui dont cette ce ces entre il elle ils elles son sa ses leur leurs ne pas plus en à "
              "de d l qu se a peut fait".split()),
}
METRICS = ("paragraphs_draft", "paragraphs_reference", "length_ratio", "opening_overlap", "content_f1",
           "statute_recall", "bge_recall", "glossary_recall", "glossary_warnings")


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


def content_words(text: str, lang: str) -> Counter:
    return Counter(w for w in re.findall(r"[^\W\d_]{3,}", text.casefold()) if w not in STOPWORDS[lang])


def f1(a: Counter, b: Counter) -> float | None:
    overlap = sum((a & b).values())
    if not a or not b:
        return None
    if not overlap:
        return 0.0
    precision, recall = overlap / sum(a.values()), overlap / sum(b.values())
    return round(2 * precision * recall / (precision + recall), 3)


def recall(expected: set, found: set) -> float | None:
    return round(len(expected & found) / len(expected), 3) if expected else None


def citations(text: str) -> tuple[set[str], set[str]]:
    cited = extract(text)
    return ({c.canonical for c in cited if c.kind == "statute"}, {c.canonical for c in cited if c.kind == "bge"})


def compare(draft: str, reference: str, lang: str) -> dict[str, Any]:
    """Deterministic style and precision metrics of one draft against Suva's summary."""
    draft_paragraphs, reference_paragraphs = paragraphs(draft), paragraphs(reference)
    draft_statutes, draft_bge = citations(draft)
    reference_statutes, reference_bge = citations(reference)
    return {
        "paragraphs_draft": len(draft_paragraphs),
        "paragraphs_reference": len(reference_paragraphs),
        "length_ratio": round(len(draft.strip()) / len(reference.strip()), 3) if reference.strip() else None,
        "opening_overlap": f1(content_words(draft_paragraphs[0] if draft_paragraphs else "", lang),
                              content_words(reference_paragraphs[0] if reference_paragraphs else "", lang)),
        "content_f1": f1(content_words(draft, lang), content_words(reference, lang)),
        "statute_recall": recall(reference_statutes, draft_statutes),
        "bge_recall": recall(reference_bge, draft_bge),
        "glossary_recall": recall(glossary.terms_in(reference, lang), glossary.terms_in(draft, lang)),
        "glossary_warnings": len(glossary.warnings(draft, lang)),
    }


@dataclass
class EvalReport:
    split: str
    model: str
    cases: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)

    @property
    def failures(self) -> int:
        return sum(1 for case in self.cases for lang in LANGUAGES if case[lang].get("error"))

    def aggregate(self) -> dict[str, dict[str, float | None]]:
        """Mean of each metric per language over the drafts that succeeded (None metrics left out)."""
        out = {}
        for lang in LANGUAGES:
            rows = [case[lang]["metrics"] for case in self.cases if "metrics" in case[lang]]
            out[lang] = {"drafts": len(rows)} | {
                metric: (round(mean(values), 3) if (values := [r[metric] for r in rows if r[metric] is not None])
                         else None) for metric in METRICS}
        return out

    def as_dict(self) -> dict[str, Any]:
        return {"split": self.split, "model": self.model, "aggregate": self.aggregate(),
                "failures": self.failures, "skipped": self.skipped, "cases": self.cases}


def evaluate(repo, config: dict, split: str = "eval", limit: int | None = None,
             make_facts: Callable | None = None, draft: Callable | None = None) -> EvalReport:
    """Draft every example of `split` from its stored judgement text and compare with Suva's summary."""
    import summaries

    make_facts = make_facts or summaries.make_facts
    draft = draft or summaries.draft
    style_examples = repo.list_examples(split="fewshot")  # the only examples a prompt may see
    report = EvalReport(split, config.get("LLM_NAME", ""))
    for example in repo.list_examples(split=split)[:limit]:
        key = example["decision_id"]
        judgement = repo.get_judgement(key)
        text = (judgement or {}).get("text") or ""
        if not text.strip():
            report.skipped.append({"key": key, "reason": "no stored judgement text (run `suva fetch`)"})
            continue
        record = {"key": key, "docket": example["docket"], "full_text": text}
        case: dict[str, Any] = {"key": key}
        try:
            facts = make_facts(config, text)
        except (OSError, ValueError) as exc:
            log.warning("eval_facts_failed case=%s error=%s", key, type(exc).__name__)
            facts = None
        for lang in LANGUAGES:
            reference = example.get(f"text_{lang}") or ""
            if not reference.strip():
                case[lang] = {"error": "no Suva summary in this language"}
                continue
            if facts is None:
                case[lang] = {"error": "factual brief failed"}
                continue
            try:
                result = draft(config, record, facts, lang, style_examples)
            except (OSError, ValueError) as exc:
                log.warning("eval_draft_failed case=%s lang=%s error=%s", key, lang, type(exc).__name__)
                case[lang] = {"error": "draft failed"}
                continue
            case[lang] = {"summary": result["summary"], "metrics": compare(result["summary"], reference, lang)}
        report.cases.append(case)
    return report


def format_report(report: EvalReport) -> str:
    def show(value):
        return "—" if value is None else f"{value:g}" if isinstance(value, (int, float)) else str(value)

    lines = [f"split={report.split} model={report.model} cases={len(report.cases)} "
             f"failures={report.failures} skipped={len(report.skipped)}"]
    short = ("length_ratio", "opening_overlap", "content_f1", "statute_recall", "bge_recall", "glossary_recall")
    header = f"{'case':<28} {'lang':<4} {'paragraphs':>10} " + " ".join(f"{m:>15}" for m in short)
    lines.append(header)
    for case in report.cases:
        for lang in LANGUAGES:
            entry = case[lang]
            if "metrics" not in entry:
                lines.append(f"{case['key']:<28} {lang:<4} {entry['error']}")
                continue
            m = entry["metrics"]
            lines.append(f"{case['key']:<28} {lang:<4} {m['paragraphs_draft']:>4} / {m['paragraphs_reference']:<3} "
                         + " ".join(f"{show(m[k]):>15}" for k in short))
    for lang, values in report.aggregate().items():
        lines.append(f"aggregate {lang}: drafts={values['drafts']} " +
                     " ".join(f"{k}={show(values[k])}" for k in METRICS))
    for item in report.skipped:
        lines.append(f"skipped {item['key']}: {item['reason']}")
    return "\n".join(lines)
