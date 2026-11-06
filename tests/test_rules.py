from pathlib import Path

from geoassist.ingest.rules import CorpusRules

RULES = Path(__file__).parent / "fixtures" / "corpus_rules.yml"


def test_suffix_and_accent_insensitive_matching():
    rules = CorpusRules.load(RULES)
    rel = "data_source/manuales/OXXO/GEOPOS - OK/VERSIONES APROBADAS - OK/a.md"
    assert rules.app_for(rel) == "geopos"
    assert rules.status_for(rel) == "approved"


def test_pending_is_evaluated_before_approved():
    rules = CorpusRules.load(RULES)
    rel = "data_source/manuales/OXXO/GEOPRICER - OK/VERSIONES POR APROBAR - OK/a.md"
    assert rules.status_for(rel) == "pending"


def test_template_is_ignored():
    rules = CorpusRules.load(RULES)
    assert rules.is_ignored("data_source/manuales/OXXO/model_manuales.md") is True
