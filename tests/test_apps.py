from pathlib import Path

from geoassist.ingest.rules import CorpusRules
from geoassist.rag.apps import build_app_detector

RULES = CorpusRules.load(Path(__file__).parent / "fixtures" / "corpus_rules.yml")


def test_detects_canonical_names_and_aliases():
    detector = build_app_detector(RULES)
    assert detector.detect("Geopricer no carga") == "geopricer"
    assert detector.detect("el módulo geoprice falla") == "geopricer"
    assert detector.detect("Error en GEOPOS") == "geopos"


def test_generic_terms_do_not_filter():
    detector = build_app_detector(RULES)
    assert detector.detect("no abre la caja") is None
    assert detector.detect("error en backoffice") is None
    assert detector.detect("problema en POS") is None


def test_word_boundaries_are_respected():
    detector = build_app_detector(RULES)
    assert detector.detect("geopostal no es una app") is None
