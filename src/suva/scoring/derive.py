"""Facts derived from a dataset record for significance rules.

The topic rules say *what* a judgement is about. These fields help say whether it *matters*:

  appellant         who brought the appeal: the party named before "gegen / contre / contro",
                    without their lawyer and role. Private persons are anonymised ("A.________"),
                    institutions (Suva, other insurers, IV offices) are named.
  outcome           how the court ruled on the appeal, as a list: upheld, partly_upheld,
                    dismissed, not_entered, written_off, sent_back. A ruling can have several
                    (e.g. upheld and sent back to the lower court).
  reasoning_pages   length of the court's reasoning, in pages of about 3,000 characters.

They are derived when the dataset is loaded, from fields it already holds, so changing
this module needs neither a new parse nor a dataset rebuild.
"""

from __future__ import annotations

import re
from typing import Any

CHARS_PER_PAGE = 3000

SIDE_SEPARATORS = re.compile(r"\|\s*(?:gegen|contre|contro)\s*\|", re.I)
# Segments that end the appellant's own name and address: their lawyer or their procedural role.
NOT_THE_PARTY = re.compile(
    r"^(?:vertreten|vertr\.|représentée?s?|représenté|patrocinat|rappresentat|"
    r"beschwerdeführer|recourante?s?|ricorrente|gesuchsteller|requérante?s?|istante)", re.I)

APPEAL_WORDS = re.compile(r"\b(?:Beschwerde|recours|ricorso|Revisionsgesuch|révision|revisione|Verfahren|"
                          r"procédure|causa)\b", re.I)
OTHER_REQUESTS = re.compile(r"Rechtspflege|assistance judiciaire|assistenza giudiziaria|Gerichtskosten|"
                            r"frais|spese|Ausstand|récusation|ricusa", re.I)
OUTCOME_PATTERNS = (  # checked per sentence that is about the appeal; order matters
    ("partly_upheld", re.compile(r"teilweise gutgeheissen|partiellement admis|parzialmente accolto", re.I)),
    ("upheld", re.compile(r"\bgutgeheissen\b|\badmise?\b|\baccolto\b", re.I)),
    ("not_entered", re.compile(r"nicht eingetreten|\birrecevables?\b|\binammissibile\b", re.I)),
    ("written_off", re.compile(r"abgeschrieben|\brayée?\b|stralciat|gegenstandslos|sans objet|privo d'oggetto", re.I)),
    ("dismissed", re.compile(r"\babgewiesen\b|\brejetée?\b|\brespinto\b", re.I)),
)
SENT_BACK = re.compile(r"zurückgewiesen|\brenvoyée?\b|\brinviat[ao]\b", re.I)


def appellant(parties: str) -> str:
    """Name and address of the party that appealed ('' if the parties cannot be split)."""
    sides = SIDE_SEPARATORS.split(parties or "", maxsplit=1)
    if len(sides) < 2:
        return ""
    kept = []
    for segment in (s.strip() for s in sides[0].split("|")):
        if NOT_THE_PARTY.match(segment):
            break
        kept.append(segment)
    return " | ".join(s for s in kept if s)


def outcome(dispositiv: str) -> list[str]:
    """How the court ruled on the appeal, from the sentences of the ruling that concern it.

    Sentences about other requests (legal aid, costs) are ignored, so "das Gesuch um
    unentgeltliche Rechtspflege wird abgewiesen" does not count as a dismissed appeal.
    """
    found: list[str] = []
    for sentence in re.split(r"(?<=[.;:])\s+", dispositiv or ""):
        if not APPEAL_WORDS.search(sentence) or OTHER_REQUESTS.search(sentence):
            continue
        for label, pattern in OUTCOME_PATTERNS:
            if pattern.search(sentence):
                if label not in found:
                    found.append(label)
                break
    if SENT_BACK.search(dispositiv or ""):
        found.append("sent_back")
    return found


def reasoning_pages(considerations: str) -> int:
    return len(considerations or "") // CHARS_PER_PAGE


def with_derived_fields(record: dict[str, Any]) -> dict[str, Any]:
    record["appellant"] = appellant(record.get("parties") or "")
    record["outcome"] = outcome(record.get("dispositiv") or "")
    record["reasoning_pages"] = reasoning_pages(record.get("considerations") or "")
    return record
