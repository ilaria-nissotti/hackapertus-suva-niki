"""Start the review app on fixed, fictional data for the browser tests (`npm --prefix src/frontend run test:ui`).

    python scripts/ui_fixture.py --port 5180

Builds a scratch knowledge base with a few fictional judgements (Relevant, Review and Low relevance) and one
approved German summary, then serves the app on it with the reference date pinned. Apertus points at a closed
local port and ElevenLabs is unconfigured, so nothing leaves the machine. Never touches `data/`.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 11, 27)
MODEL = "ui-fixture-model"

# Fictional dockets and parties: only ever stored in the scratch directory.
CASES = [
    ("8C_901/2026", date(2026, 10, 1), "Unfallversicherung (Unfallkausalität)", 88.0, "high"),
    ("8C_902/2026", date(2026, 9, 15), "Unfallversicherung (Integritätsentschädigung)", 72.0, "high"),
    ("9C_903/2026", date(2026, 10, 20), "Invalidenversicherung (Invalidenrente)", 45.0, "check"),
    ("2C_904/2026", date(2026, 8, 30), "Steuerrecht (Grundstückgewinnsteuer)", 8.0, "low"),
]
APPROVED = ("Das Bundesgericht weist die Beschwerde ab. Der natürliche Kausalzusammenhang zwischen dem Sturz und "
            "den geltend gemachten Beschwerden ist nicht mit überwiegender Wahrscheinlichkeit erstellt.")
EVIDENCE = "Die Beschwerde wird abgewiesen."


def judgement_text(docket: str, decided: date, subject: str) -> str:
    return (f"Bundesgericht\n\n{docket}\n\nUrteil vom {decided.day}. Oktober 2026\n\nVerfahrensbeteiligte\n\n"
            "A.________,\n\ngegen\n\nSuva, Fluhmattstrasse 1, 6002 Luzern,\n\n"
            f"Gegenstand\n\n{subject}, Beschwerde gegen das Urteil des Versicherungsgerichts des Kantons X.\n\n"
            "Sachverhalt:\n\nA. Die versicherte Person stürzte bei der Arbeit.\n\nErwägungen:\n\n1.\n\n"
            "Gemäss Art. 6 Abs. 1 UVG werden die Versicherungsleistungen bei Berufsunfällen gewährt.\n\n"
            "2.\n\nDie ärztlichen Berichte bestätigen den Sturz. Sie stellen jedoch keinen natürlichen "
            "Kausalzusammenhang zwischen dem Unfall und den fortbestehenden Beschwerden fest. Die übrigen "
            "Akten enthalten keine zeitnahen Befunde, die eine andere Beurteilung erlauben. Die Vorinstanz "
            "durfte deshalb auf die vorhandenen Berichte abstellen.\n\n"
            f"Demnach erkennt das Bundesgericht:\n\n1.\n\n{EVIDENCE}")


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]  # released again: nothing listens, so every Apertus call fails fast


# The fictional reviewer the browser tests enter on the start page (src/frontend/e2e/auth.setup.ts); the
# pre-approved summary is signed with it.
REVIEWER = {"name": "Muster Anna", "initials": "MUA", "function": "Rechtsanwältin"}


def build(work: Path, llm_base_url: str) -> None:
    sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
    from conftest import seed_kb  # the same seeding the review app tests use

    import jobs
    import review_kb
    import review_store
    from suva.sources.base import SourceRecord

    kb_path = work / "kb.sqlite"
    keys = [seed_kb(kb_path, docket, judgement_text(docket, decided, subject), decided, score, band)
            for docket, decided, subject, score, band in CASES]
    with closing(review_kb.open_kb(kb_path)) as repo:
        repo.upsert_judgement(SourceRecord("opencaselaw", "historical", "8C_905/2026", date(2026, 3, 1),
                                           publication_date=date(2026, 6, 30)))
        record = review_kb.get_record(repo, keys[0])
    version = jobs.generation_version(record, {"LLM_NAME": MODEL, "LLM_BASE_URL": llm_base_url})
    with closing(review_store.connect(work / "review.sqlite")) as db:
        review_store.save_result(db, keys[0], "de", version,
                                 json.dumps({"summary": APPROVED, "quotes": [EVIDENCE]}, ensure_ascii=False), None)
        assert review_store.approve(db, keys[0], "de", version, APPROVED, "", REVIEWER)
        assert sqlite3.connect(work / "review.sqlite").execute("SELECT COUNT(*) FROM results").fetchone()[0] == 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=5180)
    args = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix="suva-ui-"))
    llm_base_url = f"http://127.0.0.1:{closed_port()}/v1"
    env = os.environ | {
        "DATA_DIR": str(work), "DATABASE_URL": f"sqlite:///{work / 'kb.sqlite'}",
        "LLM_NAME": MODEL, "LLM_BASE_URL": llm_base_url, "LLM_API_KEY": "ui-fixture", "LLM_MAX_RETRIES": "0",
        # Set but empty, so values in .env can't switch audio on.
        "ELEVENLABS_API_KEY": "", "ELEVENLABS_VOICE_ID_DE": "", "ELEVENLABS_VOICE_ID_FR": "",
    }
    os.environ.update(env)  # build() imports the app modules, which read these
    build(work, llm_base_url)
    print(f"ui_fixture: scratch data in {work}", flush=True)
    os.execve(sys.executable, [sys.executable, str(ROOT / "src" / "app.py"), "--today", TODAY.isoformat(),
                               "serve", "--port", str(args.port)], env)


if __name__ == "__main__":
    main()
