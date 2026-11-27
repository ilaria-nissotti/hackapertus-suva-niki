"""Extract statute, leading-decision (BGE) and judgement references from text.

Used by scoring (step 5: e.g. "Art. 6 Abs. 2 UVG cited in the Erwägungen")
and by summary checks (step 7: every Art./BGE in a summary must exist in the
source). Law abbreviations come from a closed DE/FR/IT list and are mapped to
one German canonical name, so "art. 6 al. 2 LAA" and "Art. 6 Abs. 2 UVG" are
the same citation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from suva.sources.base import DOCKET_RE

# canonical (DE) -> aliases in DE / FR / IT
LAWS: dict[str, tuple[str, ...]] = {
    "UVG": ("UVG", "LAA", "LAINF"), "UVV": ("UVV", "OLAA", "OAINF"),
    "ATSG": ("ATSG", "LPGA"), "ATSV": ("ATSV", "OPGA"),
    "IVG": ("IVG", "LAI"), "IVV": ("IVV", "RAI", "OAI"),
    "AHVG": ("AHVG", "LAVS"), "AHVV": ("AHVV", "RAVS", "OAVS"),
    "KVG": ("KVG", "LAMal"), "KVV": ("KVV", "OAMal"),
    "BVG": ("BVG", "LPP"), "BVV 2": ("BVV 2", "OPP 2"),
    "ELG": ("ELG", "LPC"), "EOG": ("EOG", "LAPG", "LIPG"), "FZG": ("FZG", "LFLP"),
    "MVG": ("MVG", "LAM"), "AVIG": ("AVIG", "LACI", "LADI"), "VVG": ("VVG", "LCA"),
    "BGG": ("BGG", "LTF"), "BV": ("BV", "Cst.", "Cost."), "OR": ("OR", "CO"), "ZGB": ("ZGB", "CC"),
    "StGB": ("StGB", "CP"), "StPO": ("StPO", "CPP"), "ZPO": ("ZPO", "CPC"), "VwVG": ("VwVG", "PA"),
    "SchKG": ("SchKG", "LP", "LEF"), "EMRK": ("EMRK", "CEDH", "CEDU"), "AIG": ("AIG", "LEI", "LStrI"),
    "AsylG": ("AsylG", "LAsi"), "DSG": ("DSG", "LPD"), "VGG": ("VGG", "LTAF"),
}
ALIAS = {a: canon for canon, aliases in LAWS.items() for a in aliases}
_LAW_ALT = "|".join(re.escape(a) for a in sorted(ALIAS, key=len, reverse=True))

_QUAL = (r"(?:Abs\.|al\.|cpv\.|lit\.|let\.|lett\.|Ziff\.|ch\.|n\.|Satz|phrase|frase|ff\.|f\.|ss\.?|segg?\.|"
         r"und|et|e|bis|ter|quater|\d+[a-z]?(?=[\s,.)]|$)|[a-z](?=[\s,)])|,)")
ART_RE = re.compile(
    r"\b(?i:art(?:s|t)?)\.\s*(?P<art>\d+[a-z]?(?:bis|ter|quater|quinquies|sexies)?)"
    rf"(?P<qual>(?:\s*{_QUAL}){{0,14}}?)\s*"
    r"(?:des\s+|der\s+|de\s+la\s+|du\s+|de\s+l'|della\s+|del\s+|dell')?"
    rf"(?P<law>{_LAW_ALT})(?![A-Za-z])"
)
ARTICLE_LIST_RE = re.compile(r"(?:\s*(?:,|und\b|et\b|e\b)\s*\d+[a-z]?)+\s*")
ARTICLE_NUMBER_RE = re.compile(r"\d+[a-z]?")
PARA_RE = re.compile(r"(?:Abs\.|al\.|cpv\.)\s*(\d+[a-z]?)")
BGE_RE = re.compile(
    r"\b(?P<coll>BGE|ATF|DTF)\s+(?P<vol>\d{1,3})\s+(?P<part>Ia|Ib|II|III|IV|V|VI|I)\s+(?P<page>\d{1,4})\b"
    r"(?:\s*(?:E\.|consid\.|cons\.)\s*(?P<cons>\d+(?:\.\d+)*))?"
)


@dataclass(frozen=True)
class Citation:
    kind: str  # statute | bge | judgement
    canonical: str  # "Art. 6 Abs. 2 UVG" | "BGE 134 V 109" | "8C_129/2025"
    raw: str
    start: int
    end: int
    law: str | None = None  # canonical law, e.g. "UVG"
    article: str | None = None
    consideration: str | None = None  # BGE "E. 2.1"


def extract(text: str, self_docket: str | None = None, self_bge: str | None = None) -> list[Citation]:
    """All citations in text, skipping the decision's own docket and own BGE reference
    (a BGE print carries page markers like 'BGE 151 V 1 S. 3' on every page)."""
    out: list[Citation] = []
    for m in ART_RE.finditer(text):
        law = ALIAS[m["law"]]
        para = PARA_RE.search(m["qual"] or "")
        articles = [m["art"]]
        if ARTICLE_LIST_RE.fullmatch(m["qual"]):
            articles.extend(ARTICLE_NUMBER_RE.findall(m["qual"]))
        for article in articles:
            canon = f"Art. {article}" + (f" Abs. {para.group(1)}" if para else "") + f" {law}"
            out.append(Citation("statute", canon, m.group(0), m.start(), m.end(), law=law, article=article))
    for m in BGE_RE.finditer(text):
        canon = f"BGE {m['vol']} {m['part']} {m['page']}"
        if canon == self_bge:
            continue
        out.append(Citation("bge", canon, m.group(0), m.start(), m.end(), consideration=m["cons"]))
    for m in DOCKET_RE.finditer(text):
        docket = m.group()
        if docket != self_docket:
            out.append(Citation("judgement", docket, m.group(0), m.start(), m.end()))
    return sorted(out, key=lambda c: c.start)
