"""Split DE, FR, and IT decisions into verbatim passages with source offsets.

Also handles the print layout of published leading decisions (BGE): they open
with "Urteilskopf" and a Regeste (the court's headnote), use German labels
"Sachverhalt ab Seite N" / "Erwägungen" in every language, publish only an
extract of the reasoning (so the first consideration may be E. 5) and usually
carry no Dispositiv.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from suva.sources.quality import is_error_page

PARSER_VERSION = "segment-v4"
MIN_TEXT_CHARS = 1000

FACTS_RE = re.compile(r"^(Sachverhalt|Faits|Fatti)(\s+ab Seite \d+)?\s*:?$", re.I)
REASONS_RE = re.compile(
    r"^(Erwägung(?:en?)?(?:\s+ab Seite \d+)?|Considérant en droit|Considérants?|Considérant en fait et en droit|"
    r"Considerando in diritto|Considerando in fatto e in diritto|Diritto|in Erwägung,?|"
    r"Ritenuto in fatto e considerando in diritto|Ritenuto in fatto e in diritto|ritiene|"
    r"considérant,?|considerando,?|"
    # single-judge orders: "Le Président :", "La Juge présidant:", "Die Präsidentin zieht in Erwägung:"
    # (a name after the colon would be the signature, so the colon must end the line)
    r"[^.:]{0,60}\bzieht in Erwägung\s*:|"
    r"(Le|La|Il|Der|Die|Das)\s[^.:]{0,40}\b(Präsident(in)?|Einzelrichter(in)?|"
    r"Instruktionsrichter(in)?|Président(e)?|Juge|Presidente|Giudice)\b[^.:]{0,25}:)\s*:?$",
    re.I,
)
DISPOSITIV_RE = re.compile(
    r"^(Demnach (erkennt|verfügt)\b.*|Par ces motifs\b.*|Per questi motivi\b.*|"
    r"(erkennt|verfügt|prononce|pronuncia|décide|ordonne)\b[^.]{0,80}:|"
    r"(le|la|il|der|die|das)\s+[^.]{0,60}\b(erkennt|verfügt|prononce|pronuncia|décide|ordonne)\s*:)$",
    re.I,
)
# a consideration number alone ("4.1.") or opening a paragraph ("4.1. Text …")
NUM_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,5})\.?(?:\s+.*)?$", re.S)

REGESTE_RE = re.compile(r"^Regeste(\s+[a-z])?$")
BGE_EXTRACT_RE = re.compile(r"^(Urteilskopf|\d+\.\s+(Auszug aus dem Urteil|Extrait de l'arrêt|Estratto della sentenza)\b)")

PARTIES_RE = re.compile(r"^(Verfahrensbeteiligte|Participants à la procédure|Partecipanti al procedimento|Parties)$", re.I)
SUBJECT_RE = re.compile(r"^(Gegenstand|Objet|Oggetto)$", re.I)
CHAMBER_RE = re.compile(r"(Abteilung|Cour de droit|Corte di diritto|Strafkammer|Cour pénale|Corte penale)", re.I)


@dataclass
class Passage:
    pid: str  # "<key>#E.3.2"
    kind: str  # header | regeste | facts | reasons | consideration | dispositiv | full
    number: str | None  # "3.2" for considerations
    text: str
    start: int  # char offsets into the original text
    end: int


@dataclass
class Segmentation:
    key: str
    status: str  # ok | partial | failed
    issues: list[str] = field(default_factory=list)
    passages: list[Passage] = field(default_factory=list)
    chamber: str | None = None  # from the decision's own header
    parties: str | None = None
    subject: str | None = None
    parser_version: str = PARSER_VERSION


@dataclass
class _Para:
    text: str  # whitespace-normalised
    start: int
    end: int


def paragraphs(text: str) -> list[_Para]:
    """Blank-line separated paragraphs; single line breaks (around links) become spaces."""
    out = []
    for m in re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|\s*\Z)", text, re.S):
        norm = re.sub(r"\s+", " ", m.group(0)).strip()
        norm = re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", norm))
        out.append(_Para(norm, m.start(), m.end()))
    return out


def _plausible_next(prev: tuple[int, ...] | None, cand: tuple[int, ...], extract: bool = False) -> bool:
    """cand may follow prev: first child, next sibling, or next at a higher level.

    A BGE extract may start anywhere ("Aus den Erwägungen: 6. …"), so its first
    consideration can be any top-level number or that number's first child.
    """
    if prev is None:
        if extract:
            return len(cand) == 1 or (len(cand) == 2 and cand[1] == 1)
        return cand in ((1,), (1, 1))
    if cand == prev + (1,):
        return True
    return any(cand == prev[:i] + (number + 1,) for i, number in enumerate(prev))


def _find(paras: list[_Para], rx: re.Pattern, start: int = 0, max_len: int = 160) -> int | None:
    for i in range(start, len(paras)):
        if len(paras[i].text) <= max_len and rx.match(paras[i].text):
            return i
    return None


def _header_fields(seg: Segmentation, header: list[_Para]) -> None:
    texts = [p.text for p in header]
    for t in texts[:12]:
        if CHAMBER_RE.search(t) and len(t) < 120:
            seg.chamber = t
            break
    pi = next((i for i, t in enumerate(texts) if PARTIES_RE.match(t)), None)
    si = next((i for i, t in enumerate(texts) if SUBJECT_RE.match(t)), None)
    if pi is not None:
        seg.parties = " | ".join(texts[pi + 1: si if si and si > pi else len(texts)]) or None
    if si is not None and si + 1 < len(texts):
        seg.subject = " ".join(texts[si + 1:])


ORDER_CLAUSE = ("dass ", "que ", "qu'", "qu’", "che ", "vu ", "visto ", "in erwägung", "considérant", "considerando")


def _reason_spans(paras: list[_Para], start: int, end: int, extract: bool = False):
    """Yield the reasoning introduction and each numbered consideration."""
    number = None
    first = start
    for i in range(start + 1, end):
        match = NUM_RE.match(paras[i].text)
        if match:
            candidate = tuple(map(int, match[1].split(".")))
            if _plausible_next(number, candidate, extract):
                yield first, i, number
                first, number = i, candidate
    yield first, end, number


def segment(key: str, text: str | None) -> Segmentation:
    if not text or not text.strip():
        return Segmentation(key, "failed", issues=["no_text"])
    if is_error_page(text):
        return Segmentation(key, "failed", issues=["source_error_page"])

    seg = Segmentation(key, "ok")
    short_text = len(text) < MIN_TEXT_CHARS
    if short_text:
        seg.issues.append("too_short")
    paras = paragraphs(text)
    extract = any(BGE_EXTRACT_RE.match(p.text) for p in paras[:4])
    if extract:
        seg.issues.append("bge_extract")
    reasons = _find(paras, REASONS_RE)
    outcome = _find(paras, DISPOSITIV_RE, 0 if reasons is None else reasons + 1, max_len=200)
    body_end = len(paras) if outcome is None else outcome

    spans = []
    if reasons is None:
        subject = _find(paras, SUBJECT_RE)
        header_end = min(subject + 2, body_end) if subject is not None and subject + 2 <= len(paras) else 0
        spans.append(("full", header_end, body_end, None))
        seg.issues.append("no_reasons_heading")
        seg.status = "failed"
    else:
        facts = _find(paras[:reasons], FACTS_RE)
        header_end = reasons if facts is None else facts
        regeste = _find(paras[:header_end], REGESTE_RE) if extract else None
        if regeste is not None:
            spans.append(("regeste", regeste, header_end, None))
            header_end = regeste
        if facts is None:
            seg.issues.append("no_facts_heading")
        else:
            spans.append(("facts", facts, reasons, None))

    if outcome is None and not extract:  # published extracts normally omit the Dispositiv
        seg.issues.append("no_dispositiv")
    if reasons is not None:
        reason_spans = list(_reason_spans(paras, reasons, body_end, extract))
        for first, last, number in reason_spans:
            label = ".".join(map(str, number)) if number is not None else None
            spans.append(("consideration" if label else "reasons", first, last, label))
        unnumbered = len(reason_spans) == 1
        if unnumbered:
            seg.issues.append("no_numbered_considerations")
        short_order = any(p.text.lower().startswith(ORDER_CLAUSE) for p in paras[reasons + 1:min(reasons + 6, body_end)])
        if short_text or (outcome is None and not extract) or (unnumbered and not short_order):
            seg.status = "partial"

    _header_fields(seg, paras[:header_end])
    for kind, first, last, label in [("header", 0, header_end, None), *spans,
                                     ("dispositiv", body_end, len(paras), None)]:
        if first < last:
            start, end = paras[first].start, paras[last - 1].end
            suffix = f"E.{label}" if label else kind
            seg.passages.append(Passage(f"{key}#{suffix}", kind, label, text[start:end], start, end))
    return seg
