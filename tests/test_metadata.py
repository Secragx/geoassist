from pathlib import Path

from geoassist.ingest.metadata import MetadataResolver
from geoassist.ingest.rules import CorpusRules

RULES = CorpusRules.load(Path(__file__).parent / "fixtures" / "corpus_rules.yml")
REL = "data_source/manuales/OXXO/GEOPOS - OK/VERSIONES APROBADAS - OK/SopPro_Error_Acceso.md"


def test_frontmatter_wins_over_path():
    meta = MetadataResolver(RULES).resolve(
        REL,
        {"app": "geopricer", "status": "pending", "title": "Manual real", "version": "2.0"},
        first_h1="Objeto",
    )
    assert (meta.app, meta.status, meta.title, meta.version) == (
        "geopricer",
        "pending",
        "Manual real",
        "2.0",
    )


def test_placeholder_title_falls_back_to_filename():
    meta = MetadataResolver(RULES).resolve(REL, {"title": "Nombre del Manual"}, first_h1="Objeto")
    assert meta.title == "SopPro Error Acceso"


def test_generic_heading_is_not_used_as_title():
    meta = MetadataResolver(RULES).resolve(REL, {"title": "Nombre del Manual"}, first_h1="Alcance")
    assert meta.title == "SopPro Error Acceso"


def test_unknown_app_and_status_are_reported_not_raised():
    meta = MetadataResolver(RULES).resolve("otra/ruta.md", {}, first_h1=None)
    assert meta.app == "unknown"
    assert meta.status == "unknown"


def test_alias_is_canonicalized():
    meta = MetadataResolver(RULES).resolve(REL, {"app": "geoprice"}, first_h1=None)
    assert meta.app == "geopricer"
