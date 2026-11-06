from geoassist.rag.retriever import Retriever
from geoassist.rag.vector_store import VectorHit
from geoassist.storage.corpus_read import ChunkView, LexicalHit


class FakeEmbedder:
    dimension = 4

    def embed_query(self, text):
        return [1.0, 0.0, 0.0, 0.0]


class FakeStore:
    def search(self, vector, limit):
        return [VectorHit(1, 0.9), VectorHit(2, 0.8), VectorHit(3, 0.7), VectorHit(4, 0.6)]


class FakeReader:
    def lexical_search(self, query, limit, apps=None, statuses=None):
        return [LexicalHit(2, 5.0), LexicalHit(3, 4.0)]

    def get_chunks(self, ids):
        rows = {
            1: ChunkView(
                1, "d1:0", "d1", "H", "texto uno", "Doc uno", "1.0", "approved", "geopos", "a.md"
            ),
            2: ChunkView(
                2, "d2:0", "d2", "H", "texto dos", "Doc dos", "1.0", "pending", "geopos", "b.md"
            ),
            3: ChunkView(
                3, "d3:0", "d3", "H", "texto tres", "Doc tres", "1.0", "archived", "geopos", "c.md"
            ),
            4: ChunkView(
                4,
                "d4:0",
                "d4",
                "H",
                "texto cuatro",
                "Doc cuatro",
                "1.0",
                "approved",
                "geopricer",
                "d.md",
            ),
        }
        return [rows[i] for i in ids]


class FakeReranker:
    def score(self, query, passages):
        # Los pasajes llegan en orden de fusión [2, 1, 4]; el reranker los reordena a [1, 2, 4].
        return [0.2, 0.9, 0.1][: len(passages)]


def make_retriever(reranker=None, min_score=0.1):
    return Retriever(FakeReader(), FakeStore(), FakeEmbedder(), reranker, min_score=min_score)


def test_filters_archived_and_reranks_remaining():
    result = make_retriever(FakeReranker()).retrieve("consulta")
    assert [c.id for c in result.chunks] == [1, 2, 4]
    assert [c.status for c in result.chunks] == ["approved", "pending", "approved"]
    assert result.sufficient is True


def test_app_filter_is_forwarded_and_applied():
    result = make_retriever(FakeReranker()).retrieve("consulta", app_filter="geopricer")
    assert [c.app for c in result.chunks] == ["geopricer"]


def test_no_candidates_abstains_without_reranker():
    class EmptyStore(FakeStore):
        def search(self, vector, limit):
            return []

    class EmptyReader(FakeReader):
        def lexical_search(self, query, limit, apps=None, statuses=None):
            return []

    result = Retriever(EmptyReader(), EmptyStore(), FakeEmbedder(), None).retrieve("nada")
    assert result.sufficient is False
    assert result.reason == "no_candidates"
    assert result.chunks == ()


def test_low_score_abstains():
    result = make_retriever(FakeReranker(), min_score=0.95).retrieve("consulta")
    assert result.sufficient is False
    assert result.reason == "low_score"


def test_max_three_chunks_per_document():
    class SameDocReader(FakeReader):
        def get_chunks(self, ids):
            return [
                ChunkView(
                    i, f"d1:{i}", "d1", "H", "texto", "Doc", "1.0", "approved", "geopos", "a.md"
                )
                for i in ids
            ]

    class FiveStore(FakeStore):
        def search(self, vector, limit):
            return [VectorHit(i, 1.0 - i / 10) for i in range(1, 6)]

    class NoLexical(SameDocReader):
        def lexical_search(self, query, limit, apps=None, statuses=None):
            return []

    retriever = Retriever(
        NoLexical(), FiveStore(), FakeEmbedder(), None, max_per_doc=3, final_limit=5
    )
    result = retriever.retrieve("consulta")
    assert len(result.chunks) == 3
    assert all(chunk.doc_id == "d1" for chunk in result.chunks)
