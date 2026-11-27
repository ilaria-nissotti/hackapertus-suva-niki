from unittest.mock import MagicMock, patch

import summaries


def test_style_examples_come_only_from_the_fewshot_split():
    repo = MagicMock()
    repo.list_examples.return_value = [{"docket": "8C_1/2025", "text_de": "Unfallbegriff Kausalität", "text_fr": "fr"}]
    summaries.examples.cache_clear()
    with patch("summaries.open_repository", return_value=repo), patch("summaries.load_settings"):
        found = summaries.similar_examples("Kausalität beim Unfallbegriff", "de", "8C_9/2026")
        own = summaries.similar_examples("Kausalität", "de", "8C_1/2025")
    summaries.examples.cache_clear()
    repo.list_examples.assert_called_once_with(split="fewshot")
    assert found == ["Unfallbegriff Kausalität"] and own == []  # the judgement's own summary is never shown
