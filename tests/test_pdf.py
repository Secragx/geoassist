from geoassist.pdf.renderer import PdfRenderer, output_path, render_key
from geoassist.pdf.images import prepare_markdown


def test_output_path_mirrors_and_replaces_extension(tmp_path):
    rel = "data_source/manuales/OXXO/GEOPOS - OK/x y (1).md"
    assert (
        output_path(tmp_path, rel) == tmp_path / "data_source/manuales/OXXO/GEOPOS - OK/x y (1).pdf"
    )


def test_render_key_changes_with_content_and_template(tmp_path):
    base = render_key("a.md", b"md", b"tpl", {"img.png": b"img"})
    assert base != render_key("a.md", b"md2", b"tpl", {"img.png": b"img"})
    assert base != render_key("a.md", b"md", b"tpl2", {"img.png": b"img"})
    assert base != render_key("a.md", b"md", b"tpl", {"img.png": b"img2"})


def test_missing_image_is_replaced_with_alt_text(tmp_path):
    markdown = "![Pantallazo del error](no-existe.png)"
    prepared = prepare_markdown(markdown, tmp_path)
    assert "Pantallazo del error" in prepared
    assert "no-existe.png" not in prepared


def test_existing_image_is_resolved_to_assets(tmp_path):
    (tmp_path / "ok.png").write_bytes(b"png")
    prepared = prepare_markdown("![ok](ok.png)", tmp_path)
    assert str(tmp_path / "ok.png") in prepared


def test_manifest_skips_unchanged_and_regenerates_changed(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    md = repo / "data_source" / "manuales" / "a.md"
    md.parent.mkdir(parents=True)
    md.write_text("# A\n\ncontenido\n")
    template = tmp_path / "template.tex"
    template.write_text("template")
    renderer = PdfRenderer(
        repo,
        "data_source/manuales",
        "data_source/assets",
        template,
        tmp_path / "pdfs",
        tmp_path / "pdf_manifest.json",
    )
    calls = []

    def fake_convert(rel_path, markdown, output, meta):
        calls.append(rel_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"%PDF-1.4")

    monkeypatch.setattr(renderer, "_convert", fake_convert)
    first = renderer.render_all()
    second = renderer.render_all()
    assert [r.status for r in first] == ["generated"]
    assert [r.status for r in second] == ["skipped"]
    md.write_text("# A\n\ncontenido cambiado\n")
    third = renderer.render_all()
    assert [r.status for r in third] == ["generated"]
    assert len(calls) == 2


def test_failure_does_not_stop_the_batch(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    root = repo / "data_source" / "manuales"
    root.mkdir(parents=True)
    (root / "a.md").write_text("# A\n")
    (root / "b.md").write_text("# B\n")
    template = tmp_path / "template.tex"
    template.write_text("template")
    renderer = PdfRenderer(
        repo,
        "data_source/manuales",
        "data_source/assets",
        template,
        tmp_path / "pdfs",
        tmp_path / "manifest.json",
    )

    def fake_convert(rel_path, markdown, output, meta):
        if rel_path.endswith("a.md"):
            raise RuntimeError("pandoc falló")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"%PDF-1.4")

    monkeypatch.setattr(renderer, "_convert", fake_convert)
    results = renderer.render_all()
    assert {r.rel_path: r.status for r in results} == {
        "data_source/manuales/a.md": "failed",
        "data_source/manuales/b.md": "generated",
    }
