"""Suva's German/French term pairs (`rules/glossary.yaml`): prompt hints, review warnings, evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re

import yaml

from suva.config import REPO_ROOT

DEFAULT_GLOSSARY_PATH = REPO_ROOT / "rules" / "glossary.yaml"
LANGUAGES = ("de", "fr")


@dataclass(frozen=True)
class Term:
    de: str
    fr: str
    flag: bool = True

    def text(self, lang: str) -> str:
        return self.de if lang == "de" else self.fr


def _pattern(term: str) -> re.Pattern:
    # Whole words; all-capital abbreviations are case-sensitive so "OR" never matches the French "or".
    flags = 0 if term.isupper() else re.IGNORECASE
    body = r"\s+".join(re.escape(part) for part in term.replace("’", "'").split())
    return re.compile(rf"(?<![\w-]){body}(?![\w-])", flags)


@lru_cache(maxsize=512)
def _compiled(term: str) -> re.Pattern:
    return _pattern(term)


def occurs(term: str, text: str) -> bool:
    return bool(_compiled(term).search(text.replace("’", "'")))


def load_glossary(path: Path | str | None = None) -> tuple[Term, ...]:
    path = Path(path) if path else DEFAULT_GLOSSARY_PATH
    return _load(str(path), path.stat().st_mtime_ns if path.exists() else 0)


@lru_cache(maxsize=4)
def _load(path: str, _mtime: int) -> tuple[Term, ...]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    terms = []
    for i, entry in enumerate(data.get("terms") or []):
        de, fr = entry.get("de"), entry.get("fr")
        if not (isinstance(de, str) and isinstance(fr, str) and de.strip() and fr.strip()):
            raise ValueError(f"{path}: term {i + 1} needs non-empty 'de' and 'fr'")
        terms.append(Term(de.strip(), fr.strip(), bool(entry.get("flag", True))))
    return tuple(terms)


def relevant_terms(*texts: str, glossary: tuple[Term, ...] | None = None) -> list[dict]:
    """Pairs whose German or French term occurs in any of the texts, as {"de", "fr"} for the prompt."""
    glossary = load_glossary() if glossary is None else glossary
    joined = "\n".join(t for t in texts if t)
    return [{"de": t.de, "fr": t.fr} for t in glossary if occurs(t.de, joined) or occurs(t.fr, joined)]


def warnings(summary: str, lang: str, glossary: tuple[Term, ...] | None = None) -> list[dict]:
    """Other-language terms in a summary: {"found", "expected"} per flagged pair."""
    glossary = load_glossary() if glossary is None else glossary
    other = "fr" if lang == "de" else "de"
    found = []
    for t in glossary:
        wrong, right = t.text(other), t.text(lang)
        if t.flag and wrong.casefold() != right.casefold() and occurs(wrong, summary):
            found.append({"found": wrong, "expected": right})
    return found


def terms_in(text: str, lang: str, glossary: tuple[Term, ...] | None = None) -> set[str]:
    """Glossary terms of `lang` that occur in the text (for evaluation)."""
    glossary = load_glossary() if glossary is None else glossary
    return {t.text(lang) for t in glossary if occurs(t.text(lang), text)}
