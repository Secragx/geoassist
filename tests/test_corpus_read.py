from pathlib import Path
import pytest
from geoassist.ingest.chunker import Chunker
from geoassist.ingest.loader import ManualLoader
from geoassist.ingest.metadata import MetadataResolver
from geoassist.ingest.pipeline import Ingestor
from geoassist.ingest.rules import CorpusRules
from geoassist.storage.corpus_read import CorpusReader
from tests.factories import HashEmbedder

ROOT = Path(__file__).parent / "fixtures" / "repo"
RULES = CorpusRules.load(Path(__file__).parent / "fixtures" / "corpus_rules.yml")


class WordCounter:
    def count(self, text):
        return len(text.split())


@pytest.fixture
def corpus(tmp_path):
    Ingestor(
        ManualLoader(ROOT, "data_source/manuales", RULES),
        Chunker(WordCounter()),
        MetadataResolver(RULES),
        HashEmbedder(),
        WordCounter(),
    ).build("v1", tmp_path)
    return CorpusReader(tmp_path / "corpus.db")


def test_lexical_search_is_accent_insensitive(corpus):
    hits = corpus.lexical_search("recepcion", limit=10)
    assert hits
    assert all(isinstance(hit.chunk_row_id, int) for hit in hits)


def test_lexical_search_filters_status_and_app(corpus):
    hits = corpus.lexical_search("manual", limit=10, apps=["geopos"], statuses=["approved"])
    assert hits
    chunks = corpus.get_chunks([hit.chunk_row_id for hit in hits])
    assert all(chunk.app == "geopos" and chunk.status == "approved" for chunk in chunks)


def test_get_chunks_preserves_requested_order(corpus):
    hits = corpus.lexical_search("manual", limit=5)
    assert hits
    ids = [hit.chunk_row_id for hit in hits]
    assert [chunk.id for chunk in corpus.get_chunks(ids)] == ids


def test_vocabulary_contains_titles_and_headings(corpus):
    vocab = corpus.vocabulary()
    assert "recepcion" in vocab
    assert "faltante" in vocab or "mercancia" in vocab
    assert all(len(term) >= 4 for term in vocab)
    assert all(term == term.lower() for term in vocab)


def test_vocabulary_excludes_stopwords_and_caps_size(corpus):
    vocab = corpus.vocabulary()
    assert "para" not in vocab
    assert "como" not in vocab
    assert "con" not in vocab
    assert len(vocab) <= 2000
