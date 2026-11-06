from geoassist.rag.lexical import build_fts_query


def test_quotes_terms_and_joins_with_or():
    assert build_fts_query("error al cargar Geopricer") == '"error" OR "cargar" OR "Geopricer"'


def test_drops_short_tokens_and_operators():
    query = build_fts_query('¿o? " OR 1=1 --')
    assert "OR 1=1" not in query
    assert '"o"' not in query


def test_empty_question_returns_empty_string():
    assert build_fts_query("   ") == ""


def test_preserves_accented_terms():
    assert build_fts_query("recepción") == '"recepción"'
