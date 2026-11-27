"""Split a BGer judgement (plain text, OpenCaseLaw) into passages.

Layout, identical in DE / FR / IT:
  header   court, docket, date, chamber, panel, parties, subject (Gegenstand)
  facts    "Sachverhalt:" / "Faits :" / "Fatti:"          (often lettered A., B.)
  reasons  "Erwägungen:" / "Considérant en droit :" / "Diritto:" with numbered
           considerations 1. / 2.1. / 4.1.3.  -> one passage per number (E.4.1.3)
  outcome  "Demnach erkennt das Bundesgericht:" / "Par ces motifs" / "Per questi motivi"

Short orders ("Nach Einsicht … in Erwägung, dass …") have unnumbered reasons.

Never lose text (decided in step 4, to avoid false negatives): every paragraph
lands in exactly one passage, and if no reasoning section is found the body
becomes one "full" passage and the decision is flagged, not dropped.

Passage ids: "<judgement key>#E.3.2", "#header", "#facts", "#reasons",
"#dispositiv", "#full".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from suva.sources.quality import is_error_page

PARSER_VERSION = "segment-v2"
MIN_TEXT_CHARS = 1000

FACTS_RE = re.compile(r"^(Sachverhalt|Faits|Fatti)\s*:?$", re.I)
REASONS_RE = re.compile(
    r"^(Erwägungen?|Erwägung|Considérant en droit|Considérants?|Considérant en fait et en droit|"
    r"Considerando in diritto|Considerando in fatto e in diritto|Diritto|in Erwägung,?|"
    r"Ritenuto in fatto e considerando in diritto|Ritenuto in fatto e in diritto|"
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
NUM_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,5})\.?(?:\s+(.*))?$", re.S)

PARTIES_RE = re.compile(r"^(Verfahrensbeteiligte|Participants à la procédure|Partecipanti al procedimento|Parties)$", re.I)
SUBJECT_RE = re.compile(r"^(Gegenstand|Objet|Oggetto)$", re.I)
CHAMBER_RE = re.compile(r"(Abteilung|Cour de droit|Corte di diritto|Strafkammer|Cour pénale|Corte penale)", re.I)


@dataclass
class Passage:
    pid: str  # "<key>#E.3.2"
    kind: str  # header | facts | reasons | consideration | dispositiv | full
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
        if norm:
            out.append(_Para(norm, m.start(), m.end()))
    return out


def _plausible_next(prev: tuple[int, ...] | None, cand: tuple[int, ...]) -> bool:
    """cand may follow prev: first child, next sibling, or next at a higher level."""
    if prev is None:
        return cand in ((1,), (1, 1))
    if cand == prev + (1,):
        return True
    for i in range(len(prev)):
        if cand == prev[:i] + (prev[i] + 1,):
            return True
    return False


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


def segment(key: str, text: str | None) -> Segmentation:
    seg = Segmentation(key=key, status="ok")
    if not text or not text.strip():
        seg.status, seg.issues = "failed", ["no_text"]
        return seg
    if is_error_page(text):
        seg.status, seg.issues = "failed", ["source_error_page"]
        return seg
    if len(text) < MIN_TEXT_CHARS:
        seg.issues.append("too_short")
    paras = paragraphs(text)

    i_reasons = _find(paras, REASONS_RE)
    i_facts = _find(paras, FACTS_RE)
    if i_facts is not None and i_reasons is not None and i_facts > i_reasons:
        i_facts = None
    i_disp = _find(paras, DISPOSITIV_RE, i_reasons + 1 if i_reasons is not None else 0, max_len=200)

    def add(kind: str, ps: list[_Para], number: str | None = None, suffix: str | None = None) -> None:
        if not ps:
            return
        start, end = ps[0].start, ps[-1].end
        seg.passages.append(Passage(f"{key}#{suffix or kind}", kind, number, text[start:end], start, end))

    if i_reasons is None:
        seg.issues.append("no_reasons_heading")
        # keep the header if we can recognise it, else everything is one passage
        sub = _find(paras, SUBJECT_RE)
        cut = sub + 2 if sub is not None and sub + 2 <= len(paras) else 0
        if i_disp is not None:
            cut = min(cut, i_disp)
        header = paras[:cut]
        add("header", header)
        _header_fields(seg, header)
        body_end = i_disp if i_disp is not None else len(paras)
        add("full", paras[cut:body_end])
        if i_disp is not None:
            add("dispositiv", paras[i_disp:])
        else:
            seg.issues.append("no_dispositiv")
        seg.status = "failed"
        return seg

    head_end = i_facts if i_facts is not None else i_reasons
    header = paras[:head_end]
    add("header", header)
    _header_fields(seg, header)
    if i_facts is not None:
        add("facts", paras[i_facts:i_reasons])
    else:
        seg.issues.append("no_facts_heading")  # common: facts merged into reasons

    reasons_end = i_disp if i_disp is not None else len(paras)
    reasons = paras[i_reasons:reasons_end]
    if i_disp is None:
        seg.issues.append("no_dispositiv")

    # split reasons into numbered considerations
    current: list[_Para] = [reasons[0]]  # the heading ("Erwägungen:") + any intro
    cur_num: tuple[int, ...] | None = None
    found_numbers = 0

    def flush() -> None:
        if cur_num is None:
            add("reasons", current)
        else:
            n = ".".join(map(str, cur_num))
            add("consideration", current, number=n, suffix=f"E.{n}")

    for p in reasons[1:]:
        m = NUM_RE.match(p.text)
        cand = tuple(int(x) for x in m.group(1).split(".")) if m else None
        if cand and _plausible_next(cur_num, cand):
            flush()
            current, cur_num = [p], cand
            found_numbers += 1
        else:
            current.append(p)
    flush()
    if found_numbers == 0:
        seg.issues.append("no_numbered_considerations")

    if i_disp is not None:
        add("dispositiv", paras[i_disp:])

    hard = {"no_dispositiv", "too_short"}
    if hard & set(seg.issues) or (found_numbers == 0 and not _is_short_order(reasons)):
        seg.status = "partial"
    return seg


ORDER_CLAUSE = ("dass ", "que ", "qu'", "qu’", "che ", "vu ", "visto ", "in erwägung", "considérant", "considerando")


def _is_short_order(reasons: list[_Para]) -> bool:
    """Orders reason in 'dass … / que … / che …' clauses, without numbered considerations."""
    return any(p.text.lower().startswith(ORDER_CLAUSE) for p in reasons[1:6])
