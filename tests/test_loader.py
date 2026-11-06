import hashlib
import os
from pathlib import Path

import pytest

from geoassist.ingest.loader import ManualLoader, extract_first_h1
from geoassist.ingest.rules import CorpusRules

ROOT = Path(__file__).parent / "fixtures" / "repo"
RULES = CorpusRules.load(Path(__file__).parent / "fixtures" / "corpus_rules.yml")


def test_discovers_only_markdown_under_manuals_root():
    docs = ManualLoader(ROOT, "data_source/manuales", RULES).discover()
    assert len(docs) == 3
    assert all(doc.rel_path.endswith(".md") for doc in docs)
    assert all(not doc.rel_path.endswith("model_manuales.md") for doc in docs)


def test_paths_are_repo_relative_posix():
    docs = ManualLoader(ROOT, "data_source/manuales", RULES).discover()
    assert docs[0].rel_path.startswith("data_source/manuales/OXXO/")


def test_crlf_and_bom_are_normalized():
    docs = ManualLoader(ROOT, "data_source/manuales", RULES).discover()
    doc = next(d for d in docs if "Acento" in d.rel_path)
    assert "\r" not in doc.text
    assert not doc.text.startswith("\ufeff")


def test_invalid_yaml_warns_and_keeps_text():
    docs = ManualLoader(ROOT, "data_source/manuales", RULES).discover()
    doc = next(d for d in docs if "YamlRoto" in d.rel_path)
    assert doc.frontmatter == {}
    assert any("yaml" in warning.lower() for warning in doc.warnings)


def test_missing_image_reference_warns():
    docs = ManualLoader(ROOT, "data_source/manuales", RULES).discover()
    doc = next(d for d in docs if "ImagenFaltante" in d.rel_path)
    assert any("imagen" in warning.lower() for warning in doc.warnings)


def test_extract_first_h1():
    assert extract_first_h1("texto\n\n# Título real\n## Sección\n") == "Título real"
    assert extract_first_h1("sin encabezados") is None


def test_extract_first_h1_ignores_fenced_code_blocks():
    markdown = "```python\n# no es un título\n```\n\n# Título real\n"
    assert extract_first_h1(markdown) == "Título real"
    assert extract_first_h1("~~~\n# tampoco\n~~~\n") is None


def test_valid_frontmatter_is_parsed_and_body_excludes_it():
    docs = ManualLoader(ROOT, "data_source/manuales", RULES).discover()
    doc = next(d for d in docs if "Acento" in d.rel_path)
    assert doc.frontmatter["app"] == "geopos"
    assert doc.frontmatter["status"] == "approved"
    assert doc.text.lstrip().startswith("# ")
    assert "recepción" in doc.text


def test_content_hash_is_sha256_of_normalized_text():
    docs = ManualLoader(ROOT, "data_source/manuales", RULES).discover()
    doc = next(d for d in docs if "Acento" in d.rel_path)
    expected = hashlib.sha256(doc.text.encode("utf-8")).hexdigest()
    assert doc.content_hash == expected


def test_paths_preserve_spaces_accents_and_parentheses():
    docs = ManualLoader(ROOT, "data_source/manuales", RULES).discover()
    paths = [doc.rel_path for doc in docs]
    assert any(
        " " in path and "(" in path and any(ord(char) > 127 for char in path) for path in paths
    )
    assert paths == sorted(paths)


def test_txt_and_template_are_not_discovered():
    docs = ManualLoader(ROOT, "data_source/manuales", RULES).discover()
    paths = [doc.rel_path for doc in docs]
    assert not any(path.endswith(".txt") for path in paths)
    assert not any(path.endswith("model_manuales.md") for path in paths)


def test_missing_image_warning_includes_source():
    docs = ManualLoader(ROOT, "data_source/manuales", RULES).discover()
    doc = next(d for d in docs if "ImagenFaltante" in d.rel_path)
    assert any("imagen ausente: OK_imagen_inexistente_img1.png" in w for w in doc.warnings)


def test_symlink_escaping_repo_root_is_ignored(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks no disponibles")
    repo = tmp_path / "repo"
    manuals = repo / "data_source" / "manuales"
    manuals.mkdir(parents=True)
    (manuals / "local.md").write_text("# Local\n", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("# Fuera\n", encoding="utf-8")
    try:
        (manuals / "escape.md").symlink_to(outside)
    except OSError:
        pytest.skip("no se pudo crear el symlink")

    docs = ManualLoader(repo, "data_source/manuales", RULES).discover()

    assert [doc.rel_path for doc in docs] == ["data_source/manuales/local.md"]


def test_missing_assets_directory_does_not_crash(tmp_path):
    repo = tmp_path / "repo"
    manuals = repo / "data_source" / "manuales"
    manuals.mkdir(parents=True)
    (manuals / "manual.md").write_text(
        "---\napp: geopos\n---\n# Manual\n\n![x](no-existe.png)\n",
        encoding="utf-8",
    )

    docs = ManualLoader(repo, "data_source/manuales", RULES).discover()

    assert len(docs) == 1
    assert any("imagen ausente" in warning for warning in docs[0].warnings)


def test_existing_asset_is_resolved_by_base_name(tmp_path):
    repo = tmp_path / "repo"
    manuals = repo / "data_source" / "manuales"
    manuals.mkdir(parents=True)
    assets = repo / "data_source" / "assets" / "capturas"
    assets.mkdir(parents=True)
    (assets / "pantalla error.png").write_bytes(b"png")
    (manuals / "manual.md").write_text(
        "---\napp: geopos\n---\n# Manual\n\n![x](<pantalla error.png>)\n",
        encoding="utf-8",
    )

    docs = ManualLoader(repo, "data_source/manuales", RULES).discover()

    assert len(docs) == 1
    assert docs[0].warnings == ()
