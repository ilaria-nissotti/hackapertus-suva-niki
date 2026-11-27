import pytest


def ex(docket="8C_1/2025", **kw):
    row = dict(docket=docket, title="BGer-Urteil", text_de="de", text_fr="fr",
               source_file="Export.html", notes=["n"])
    row.update(kw)
    return row


def test_upsert_example_roundtrip_and_update(repo):
    repo.upsert_example(ex())
    repo.upsert_example(ex(text_de="neu"))
    rows = repo.list_examples()
    assert len(rows) == 1 and rows[0]["text_de"] == "neu" and rows[0]["notes"] == ["n"]


def test_reimport_keeps_frozen_split(repo):
    repo.upsert_example(ex())
    repo.set_example_split("8C_1/2025", "eval")
    repo.upsert_example(ex(text_de="re-import"))
    assert repo.list_examples(split="eval")[0]["text_de"] == "re-import"


def test_split_cannot_move(repo):
    repo.upsert_example(ex())
    repo.set_example_split("8C_1/2025", "eval")
    repo.set_example_split("8C_1/2025", "eval")  # idempotent
    with pytest.raises(ValueError, match="frozen"):
        repo.set_example_split("8C_1/2025", "fewshot")
    with pytest.raises(KeyError):
        repo.set_example_split("9C_9/2025", "eval")


def test_unknown_column_rejected(repo):
    with pytest.raises(ValueError, match="unknown columns"):
        repo.upsert_example(ex(split="eval"))  # split only via set_example_split


def test_known_positives_filter(repo):
    repo.upsert_known_positive(dict(ref="8C_1/2025", docket="8C_1/2025", court="bger", in_xlsx=1, in_exports=0))
    repo.upsert_known_positive(dict(ref="Entscheid KG BL", docket=None, court="cantonal", in_xlsx=1, in_exports=0))
    assert [r["ref"] for r in repo.list_known_positives(court="bger")] == ["8C_1/2025"]
    assert len(repo.list_known_positives()) == 2
