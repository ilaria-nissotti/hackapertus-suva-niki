"""Questions over the whole library of approved summaries, answered by Apertus with cited cases.

Retrieval is full-text search (FTS5) over the approved summaries; only the retrieved summaries
reach the model, and every case it cites must be one of them.
"""

import logging
import re

from summaries import ask

log = logging.getLogger(__name__)  # never log questions or answers
MAX_QUESTION = 1000
MAX_SOURCES = 6
STOP = set("der die das und oder ist sind wie was wann welche wird werden nach mit von für bei ein eine "
           "le la les et ou est sont quel quelle quels comment quand selon pour avec dans des une un du".split())


def search_terms(question):
    """Content words of a question, for an any-word FTS query (BM25 ranks cases with more of them first)."""
    return " ".join(w for w in re.findall(r"[^\W_]{3,}", question) if w.casefold() not in STOP)


def answer(config, question, lang, sources):
    """{"answer", "cited"}: `sources` are library rows (case_key, summary); only their keys may be cited."""
    language = {"de": "German", "fr": "French"}[lang]
    allowed = {row["case_key"] for row in sources}
    instruction = f"""Answer the lawyer's question in {language} using only the approved
Suva summaries in "summaries". Cite every summary you rely on by its "case" value in
"cited". If the summaries do not answer the question, say so. Return a JSON object
with keys "answer" (plain text) and "cited" (array of case values)."""
    data = {"question": question,
            "summaries": [{"case": row["case_key"], "language": row["lang"], "text": row["summary"][:4000]}
                          for row in sources]}
    result = ask(config, instruction, data, 900)
    text, cited = result.get("answer"), result.get("cited")
    if not isinstance(text, str) or not text.strip() or not isinstance(cited, list):
        raise ValueError("Apertus did not return an answer with cited cases.")
    cited = [c for c in dict.fromkeys(cited) if isinstance(c, str) and c in allowed]
    if not cited:
        raise ValueError("The answer cites no case from the library.")
    return {"answer": text.strip(), "cited": cited}
