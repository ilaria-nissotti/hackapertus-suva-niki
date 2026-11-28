"""`suva score` (what the review app shows) must score like the evaluation: with the derived fields."""

from datetime import date

from suva.parse.run import parse_all
from suva.scoring.rules import load_rules
from suva.scoring.store import score_keys
from suva.sources.base import SourceRecord

LONG_UPHELD = ("Bundesgericht\n\nUrteil vom 1. Oktober 2026\n\nVerfahrensbeteiligte\n\nA.________,\n\ngegen\n\n"
               "Suva, Fluhmattstrasse 1, 6002 Luzern,\n\nGegenstand\n\nUnfallversicherung\n\n"
               "Sachverhalt:\n\nA. Der Versicherte stürzte.\n\nErwägungen:\n\n1.\n\n"
               + "Gemäss Art. 6 Abs. 1 UVG ist die Leistungspflicht zu prüfen. " * 260
               + "\n\nDemnach erkennt das Bundesgericht:\n\n1.\n\nDie Beschwerde wird gutgeheissen.")


def test_stored_scores_fire_the_significance_rules(repo):
    rec = SourceRecord(source="opencaselaw", source_id="8C_7/2026", docket="8C_7/2026",
                       decision_date=date(2026, 10, 1), text=LONG_UPHELD)
    repo.upsert_judgement(rec)
    parse_all(repo)
    score_keys(repo, [rec.key], load_rules())
    fired = {f["id"] for f in repo.get_score(rec.key)["fired"]}
    assert "long_reasoning" in fired  # reasoning_pages is a derived field
