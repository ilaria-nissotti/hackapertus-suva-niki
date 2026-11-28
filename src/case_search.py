"""Precedents for a lawyer's case: rank stored judgements against a short case description.

BM25 over the judgement text, plus a boost for each statute or leading decision (BGE) that the
description cites and the judgement cites too (from the stored parse). Every hit says why it
matched and shows the best-matching passages. No language model is involved.
"""

from __future__ import annotations

from collections import Counter
import math
import re

from suva.parse.citations import extract
from suva.summary_eval import STOPWORDS

K1, B = 1.2, 0.75
EXACT_CITATION, SAME_ARTICLE = 4.0, 2.0  # boosts, in BM25 units
STOP = STOPWORDS["de"] | STOPWORDS["fr"] | {"art", "abs", "lit", "let", "bge", "atf"}
_token_cache: dict[tuple[str, str], Counter] = {}


def tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[^\W\d_]{3,}", text.casefold()) if w not in STOP]


def _counts(key: str, text: str) -> Counter:
    marker = (key, str(hash(text)))
    if marker not in _token_cache:
        if len(_token_cache) > 5000:
            _token_cache.clear()
        _token_cache[marker] = Counter(tokens(text))
    return _token_cache[marker]


def query_citations(description: str) -> tuple[set[str], set[tuple[str, str]]]:
    cited = extract(description)
    exact = {c.canonical for c in cited if c.kind in ("statute", "bge")}
    articles = {(c.law, c.article) for c in cited if c.kind == "statute" and c.law}
    return exact, articles


def highlight(text: str, words: set[str], width: int = 280) -> str:
    """A window of `text` around its first matched word, with matched words marked ⟦…⟧."""
    pattern = re.compile(r"[^\W\d_]{3,}")
    found = [m for m in pattern.finditer(text) if m.group().casefold() in words]
    if not found:
        return text[:width].strip()
    start = max(0, found[0].start() - width // 3)
    end = min(len(text), start + width)
    out, last = [], start
    for m in found:
        if m.start() < start or m.end() > end:
            continue
        out += [text[last:m.start()], f"⟦{m.group()}⟧"]
        last = m.end()
    out.append(text[last:end])
    return ("…" if start else "") + "".join(out).strip() + ("…" if end < len(text) else "")


def search(records: list[dict], citations_of, passages_of, description: str, limit: int = 20) -> list[dict]:
    """Rank `records` (with key and full_text) for the description.

    `citations_of(key)` returns stored citation rows (kind, canonical, law, article);
    `passages_of(key)` returns stored passages (text)."""
    words = Counter(tokens(description))
    exact, articles = query_citations(description)
    if not words and not exact:
        return []
    docs = [(r, _counts(r["key"], r["full_text"])) for r in records if r["full_text"].strip()]
    if not docs:
        return []
    average = sum(sum(c.values()) for _, c in docs) / len(docs)
    frequency = Counter(w for _, counts in docs for w in words if counts[w])
    scored = []
    for rec, counts in docs:
        length = sum(counts.values()) or 1
        bm25, terms = 0.0, []
        for w in words:
            tf = counts[w]
            if not tf:
                continue
            idf = math.log(1 + (len(docs) - frequency[w] + .5) / (frequency[w] + .5))
            bm25 += idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / average))
            terms.append(w)
        matches, boost = [], 0.0
        if exact or articles:
            stored = citations_of(rec["key"])
            canonical = {c["canonical"] for c in stored}
            same_article = {(c["law"], c["article"]) for c in stored if c["kind"] == "statute"}
            exact_hits = exact & canonical
            for value in sorted(exact_hits):
                matches.append({"kind": "bge" if value.startswith("BGE") else "statute", "value": value})
                boost += EXACT_CITATION
            counted = {(c["law"], c["article"]) for c in stored if c["canonical"] in exact_hits}
            for law, article in sorted(articles - counted):
                if (law, article) in same_article:
                    matches.append({"kind": "article", "value": f"Art. {article} {law}"})
                    boost += SAME_ARTICLE
        if not terms and not matches:
            continue
        matches += [{"kind": "term", "value": w} for w in sorted(terms, key=lambda w: -counts[w])[:8]]
        scored.append((bm25 + boost, rec, matches, set(terms)))
    scored.sort(key=lambda item: item[0], reverse=True)
    hits = []
    for score, rec, matches, terms in scored[:limit]:
        ranked = sorted(passages_of(rec["key"]), key=lambda p: -sum(Counter(tokens(p["text"]))[w] for w in terms))
        hits.append({
            "key": rec["key"], "docket": rec["docket"], "title": rec["title"], "decision_date": rec["decision_date"],
            "status": rec["status"], "relevance": round(score, 2), "matches": matches,
            "highlights": [highlight(p["text"], terms) for p in ranked[:2] if p["text"].strip()],
        })
    return hits
