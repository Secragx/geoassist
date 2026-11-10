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


def _doc_chunks_reader(doc_id, only_doc=False):
    class Reader(FakeReader):
        def get_chunks(self, ids):
            rows = {}
            for i in ids:
                if only_doc or i <= 2:
                    rows[i] = ChunkView(
                        i,
                        f"{doc_id}:{i}",
                        doc_id,
                        "H",
                        "texto",
                        "Doc",
                        "1.0",
                        "approved",
                        "geopos",
                        "a.md",
                    )
                else:
                    rows[i] = ChunkView(
                        i,
                        f"d2:{i}",
                        "d2",
                        "H",
                        "texto",
                        "Doc dos",
                        "1.0",
                        "approved",
                        "geopos",
                        "b.md",
                    )
            return [rows[i] for i in ids]

        def lexical_search(self, query, limit, apps=None, statuses=None):
            return []

    return Reader()


def test_winner_case_gets_all_its_candidate_chunks():
    class FiveStore(FakeStore):
        def search(self, vector, limit):
            return [VectorHit(i, 1.0 - i / 10) for i in range(1, 6)]

    reader = _doc_chunks_reader("d1", only_doc=True)
    retriever = Retriever(reader, FiveStore(), FakeEmbedder(), None)
    result = retriever.retrieve("consulta")
    # El caso ganador llega completo: 5 chunks candidatos, no 3.
    assert len(result.chunks) == 5
    assert all(chunk.doc_id == "d1" for chunk in result.chunks)


def test_winner_case_context_is_capped():
    class EightStore(FakeStore):
        def search(self, vector, limit):
            return [VectorHit(i, 1.0 - i / 20) for i in range(1, 9)]

    reader = _doc_chunks_reader("d1", only_doc=True)
    retriever = Retriever(reader, EightStore(), FakeEmbedder(), None, max_case_chunks=6)
    result = retriever.retrieve("consulta")
    assert len(result.chunks) == 6


def test_winner_chunks_are_in_reading_order():
    class ReversedStore(FakeStore):
        def search(self, vector, limit):
            return [VectorHit(3, 0.9), VectorHit(1, 0.8), VectorHit(2, 0.7)]

    reader = _doc_chunks_reader("d1", only_doc=True)
    retriever = Retriever(reader, ReversedStore(), FakeEmbedder(), None)
    result = retriever.retrieve("consulta")
    assert [chunk.id for chunk in result.chunks] == [1, 2, 3]


def test_secondary_docs_stay_capped_at_three():
    class WideStore(FakeStore):
        def search(self, vector, limit):
            return [VectorHit(i, 1.0 - i / 20) for i in range(1, 8)]

    reader = _doc_chunks_reader("d1")
    retriever = Retriever(reader, WideStore(), FakeEmbedder(), None, final_limit=8)
    result = retriever.retrieve("consulta")
    per_doc: dict[str, int] = {}
    for chunk in result.chunks:
        per_doc[chunk.doc_id] = per_doc.get(chunk.doc_id, 0) + 1
    assert per_doc["d1"] == 2  # caso ganador completo
    assert per_doc["d2"] == 3  # secundario acotado
    assert len(result.chunks) <= 8
