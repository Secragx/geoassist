import json
import sqlite3
from pathlib import Path

import pytest

from geoassist.ingest.chunker import Chunker
from geoassist.ingest.loader import ManualLoader
from geoassist.ingest.metadata import MetadataResolver
from geoassist.ingest.pipeline import Ingestor, IngestValidationError
from geoassist.ingest.rules import CorpusRules
from tests.factories import HashEmbedder

ROOT = Path(__file__).parent / "fixtures" / "repo"
RULES = CorpusRules.load(Path(__file__).parent / "fixtures" / "corpus_rules.yml")


class WordCounter:
    def count(self, text):
        return len(text.split())


def make_ingestor(embedder=None):
    return Ingestor(
        loader=ManualLoader(ROOT, "data_source/manuales", RULES),
        chunker=Chunker(WordCounter()),
        resolver=MetadataResolver(RULES),
        embedder=embedder or HashEmbedder(),
        counter=WordCounter(),
    )


def test_build_writes_documents_chunks_fts_and_manifest(tmp_path):
    report = make_ingestor().build("v1", tmp_path)
    assert report.doc_count == 3
    assert report.chunk_count > 0
    db = sqlite3.connect(tmp_path / "corpus.db")
    assert db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 3
    assert db.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == report.chunk_count
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["version_id"] == "v1"
    assert manifest["chunk_count"] == report.chunk_count
    assert manifest["db_sha256"] and manifest["index_sha256"]


def test_template_is_not_indexed(tmp_path):
    make_ingestor().build("v1", tmp_path)
    db = sqlite3.connect(tmp_path / "corpus.db")
    paths = [row[0] for row in db.execute("SELECT rel_path FROM documents")]
    assert all("model_manuales.md" not in path for path in paths)


def test_missing_image_is_a_warning_not_a_failure(tmp_path):
    report = make_ingestor().build("v1", tmp_path)
    assert any("imagen" in warning.lower() for warning in report.warnings)


def test_dimension_mismatch_aborts_and_removes_output(tmp_path):
    class BrokenEmbedder(HashEmbedder):
        def embed_documents(self, texts):
            return [[0.1, 0.2] for _ in texts]

    with pytest.raises(IngestValidationError):
        make_ingestor(BrokenEmbedder()).build("v1", tmp_path)
    assert not (tmp_path / "corpus.db").exists()
