import pytest

from suva.scoring.compare import configurations, cross_validated_scores, fold_of, subset
from suva.scoring.derive import appellant, outcome, reasoning_pages, with_derived_fields
from suva.scoring.rules import RulesError, parse_rules


@pytest.mark.parametrize("parties, expected", [
    ("A.________, | vertreten durch Protekta Rechtsschutz-Versicherung AG, | Beschwerdeführer, | gegen | "
     "Schweizerische Unfallversicherungsanstalt (Suva), | Beschwerdegegnerin.", "A.________,"),
    ("Schweizerische Unfallversicherungsanstalt (Suva), Rechtsabteilung, Fluhmattstrasse 1, 6002 Luzern, | "
     "Beschwerdeführerin, | gegen | A.________, | Beschwerdegegner.",
     "Schweizerische Unfallversicherungsanstalt (Suva), Rechtsabteilung, Fluhmattstrasse 1, 6002 Luzern,"),
    ("Bâloise Assurance SA, | représentée par Me Michel D'Alessandri, avocat, | recourante, | contre | A.________",
     "Bâloise Assurance SA,"),
    ("A.________, | patrocinata dall'avv. Stefano Stillitano, | ricorrente, | contro | Ufficio AI", "A.________,"),
    ("A.________, | Beschwerdeführer.", ""),  # no other side: cannot tell
])
def test_appellant_is_the_party_before_the_other_side_without_its_lawyer(parties, expected):
    assert appellant(parties) == expected


@pytest.mark.parametrize("dispositiv, expected", [
    ("1.\n\nDie Beschwerde wird gutgeheissen. Das Urteil wird aufgehoben und die Sache an die Vorinstanz "
     "zurückgewiesen.\n\n2.\n\nDie Gerichtskosten werden der Suva auferlegt.", ["upheld", "sent_back"]),
    ("1.\n\nDie Beschwerde wird abgewiesen.\n\n2.\n\nDas Gesuch um unentgeltliche Rechtspflege wird abgewiesen.",
     ["dismissed"]),
    ("1.\n\nAuf die Beschwerde wird nicht eingetreten.\n\n2.\n\nEs werden keine Gerichtskosten erhoben.",
     ["not_entered"]),
    ("1.\n\nLe recours est partiellement admis. La cause est renvoyée à l'office.", ["partly_upheld", "sent_back"]),
    ("1.\n\nLe recours est irrecevable.\n\n2.\n\nLa requête d'assistance judiciaire est rejetée.", ["not_entered"]),
    ("1.\n\nIl ricorso è respinto.\n\n2.\n\nLa domanda di assistenza giudiziaria è respinta.", ["dismissed"]),
    ("1.\n\nDas Verfahren wird als gegenstandslos abgeschrieben.", ["written_off"]),
    ("", []),
])
def test_outcome_reads_only_the_sentences_about_the_appeal(dispositiv, expected):
    assert outcome(dispositiv) == expected


def test_derived_fields_are_added_to_a_record():
    record = with_derived_fields({"parties": "Suva, | Beschwerdeführerin, | gegen | A.________",
                                  "dispositiv": "Die Beschwerde wird abgewiesen.", "considerations": "x" * 9500})
    assert record["appellant"] == "Suva," and record["outcome"] == ["dismissed"] and record["reasoning_pages"] == 3
    assert reasoning_pages("") == 0


INSURER = {"id": "insurer_appeals", "group": "significance", "field": "appellant", "weight": 14,
           "match": {"any": ["Suva", "Assurance", "Versicherung"], "none": ["IV-Stelle", "assurance-invalidité"]},
           "explanation": "The insurer appealed."}


def test_none_terms_stop_a_rule_from_firing():
    rule = parse_rules({"rules": [INSURER]}).rules[0]
    assert rule.group == "significance"
    assert rule.matches({"appellant": "Bâloise Assurance SA,"})
    assert not rule.matches({"appellant": "Office de l'assurance-invalidité du canton de Genève,"})
    assert not rule.matches({"appellant": "A.________,"})


def test_number_rules_fire_from_min_and_graded_ones_count_the_number():
    yes_no, graded = parse_rules({"rules": [
        {"id": "long", "field": "reasoning_pages", "match": {"min": 4}, "weight": 4, "explanation": "Long."},
        {"id": "pages", "field": "reasoning_pages", "match": {"min": 1}, "per_match": 2, "max": 10,
         "explanation": "Pages."}]}).rules
    assert not yes_no.matches({"reasoning_pages": 3}) and yes_no.points_for(yes_no.count({"reasoning_pages": 5})) == 4
    assert graded.points_for(graded.count({"reasoning_pages": 3})) == 6
    assert graded.points_for(graded.count({"reasoning_pages": 9})) == 10  # capped


@pytest.mark.parametrize("rule, message", [
    ({"field": "reasoning_pages", "match": {"any": ["4"]}}, "needs match: {min"),
    ({"field": "subject", "match": {"min": 3}}, "only applies to number fields"),
    ({"field": "subject", "match": {"none": ["x"]}}, "match.none only excludes"),
    ({"field": "subject", "match": {"any": ["x"]}, "group": "vibes"}, "group must be one of"),
])
def test_invalid_significance_rules_are_rejected(rule, message):
    with pytest.raises(RulesError, match=message):
        parse_rules({"rules": [{"id": "r", "weight": 1, "explanation": "e"} | rule]})


RULES = {"base": 0, "rules": [
    {"id": "accident", "field": "subject", "match": {"any": ["Unfall"]}, "weight": 0, "explanation": "Accident."},
    INSURER | {"weight": 0}]}


def test_configurations_are_topic_rules_then_significance():
    ruleset = parse_rules(RULES)
    assert [c.name for c in configurations(ruleset)] == ["topic rules", "+ significance rules"]
    assert [r.id for r in subset(ruleset, configurations(ruleset)[0]).rules] == ["accident"]
    assert [r.id for r in subset(ruleset, configurations(ruleset)[1]).rules] == ["accident", "insurer_appeals"]
    topic_only = parse_rules({"rules": RULES["rules"][:1]})
    assert [c.name for c in configurations(topic_only)] == ["topic rules"]


def test_similarity_section_is_rejected_as_removed():
    with pytest.raises(RulesError, match="similarity to past picks"):
        parse_rules(RULES | {"similarity": {"signals": []}})


def test_cross_validation_scores_every_judgement_once_with_weights_fitted_elsewhere():
    def rec(i, relevant, appellant_name):
        return {"key": f"8C_{i}/2025@2025-11-01", "docket": f"8C_{i}/2025", "relevant": relevant,
                "subject": "Unfall", "appellant": appellant_name, "articles": [], "bge": []}

    records = [rec(i, True, "Suva,") for i in range(12)] + [rec(100 + i, False, "A.________,") for i in range(60)]
    ruleset = subset(parse_rules(RULES), configurations(parse_rules(RULES))[1])  # topic + significance
    scored = cross_validated_scores(records, ruleset)
    assert sorted(r["key"] for r, _ in scored) == sorted(r["key"] for r in records)
    assert min(s.rule_points for r, s in scored if r["relevant"]) > max(s.rule_points for r, s in scored if not r["relevant"])
    assert len({fold_of(r["docket"]) for r in records}) == 5
