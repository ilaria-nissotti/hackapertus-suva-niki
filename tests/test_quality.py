from suva.sources.quality import html_to_text, is_error_page, usable_text

ERROR = ("menu\n\nRechtsprechung\n\nListe der Neuheiten\n\nDokument nicht gefunden Dieser AZA-Entscheid "
         "ist in elektronischer Form nicht verfügbar.\n\nEurospider Information Technology AG")


def test_error_page_detected():
    assert is_error_page(ERROR) and usable_text(ERROR) is None


def test_real_judgement_not_flagged_even_with_portal_words():
    real = "8C_1/2025\n\nErwägungen:\n\n1.\n\nGemäss Liste der Neuheiten …\n\nDemnach erkennt das Bundesgericht:"
    assert not is_error_page(real) and usable_text(real) == real


def test_empty():
    assert usable_text("  ") is None and usable_text(None) is None and not is_error_page(None)


def test_html_to_text_one_paragraph_per_div():
    html = "<html><body><div class='para'>8C_1/2025</div><div class='para'>Erwägungen:</div><div class='para'> 1. </div></body></html>"
    assert html_to_text(html) == "8C_1/2025\n\nErwägungen:\n\n1."


def test_error_marker_is_not_hidden_by_prose_mention():
    assert is_error_page("Document introuvable. Consultez les considérants sur le portail.")
    assert not is_error_page("Document trouvé")
