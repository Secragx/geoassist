"""Pruebas de la interpretación local de consultas (jerga y typos)."""

from geoassist.rag.rewrite import LocalQueryInterpreter

ALIASES = {"geoconsola": "geopos", "consola": "geopos", "inventario": "geoinventarios"}
VOCAB = ["geopos", "geoinventarios", "devolucion", "cedis", "impresora", "remesa"]


def test_jerga_maps_to_canonical_app():
    out = LocalQueryInterpreter(ALIASES, VOCAB).interpret("la geoconsola no carga")
    assert "geopos" in out
    assert "geoconsola" not in out


def test_typo_in_product_name_is_corrected():
    out = LocalQueryInterpreter(ALIASES, VOCAB).interpret("geopcos no abre")
    assert "geopos" in out


def test_typo_in_case_term_is_corrected():
    out = LocalQueryInterpreter(ALIASES, VOCAB).interpret("no puedo hacer la devoLucion")
    assert "devolucion" in out


def test_valid_uncommon_words_are_not_mangled():
    original = "error en la impresora del local grande"
    assert LocalQueryInterpreter(ALIASES, VOCAB).interpret(original) == original


def test_short_tokens_are_untouched():
    original = "el pos no va"
    assert LocalQueryInterpreter(ALIASES, VOCAB).interpret(original) == original
