from geoassist.render.manifest import ManifestBuilder, PdfManifest, PdfManifestEntry


def test_manifest_builder_builds_entries(tmp_path):
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    (manuals / "a.md").write_text("# A\n")
    out = tmp_path / "pdfs"
    out.mkdir()
    (out / "a.pdf").write_bytes(b"%PDF-1.4")
    builder = ManifestBuilder(manuals, out)
    manifest = builder.build()
    assert isinstance(manifest, PdfManifest)
    assert len(manifest.entries) == 1
    entry = manifest.entries[0]
    assert isinstance(entry, PdfManifestEntry)
    assert entry.manual_path == "a.md"
    assert entry.pdf_path == "a.pdf"
    assert entry.source_sha256
    assert entry.pdf_sha256
    assert (out / "manifest.json").is_file()


def test_manifest_builder_function_style(tmp_path):
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    (manuals / "a.md").write_text("# A\n")
    out = tmp_path / "pdfs"
    out.mkdir()
    manifest = ManifestBuilder().build(manuals, out)
    assert manifest.generated_at
    assert len(manifest.entries) == 1
