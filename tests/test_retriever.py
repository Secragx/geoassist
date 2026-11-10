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
    def _rows(self):
        return {
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

    def lexical_search(self, query, limit, apps=None, statuses=None):
        return [LexicalHit(2, 5.0), LexicalHit(3, 4.0)]

    def get_chunks(self, ids):
        rows = self._rows()
        return [rows[i] for i in ids]

    def get_doc_chunks(self, doc_id):
        return [chunk for chunk in self._rows().values() if chunk.doc_id == doc_id]


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


def _doc_chunks_reader(doc_id, only_doc=False, universe=5):
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

        def get_doc_chunks(self, doc):
            chunks = []
            for i in range(1, universe + 1):
                if only_doc or i <= 2:
                    if doc == doc_id:
                        chunks.append(
                            ChunkView(
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
                        )
                elif doc == "d2":
                    chunks.append(
                        ChunkView(
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
                    )
            return chunks

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

    reader = _doc_chunks_reader("d1", only_doc=True, universe=8)
    retriever = Retriever(reader, EightStore(), FakeEmbedder(), None, max_case_chunks=6)
    result = retriever.retrieve("consulta")
    assert len(result.chunks) == 6


def test_winner_chunks_are_in_reading_order():
    class ReversedStore(FakeStore):
        def search(self, vector, limit):
            return [VectorHit(3, 0.9), VectorHit(1, 0.8), VectorHit(2, 0.7)]

    reader = _doc_chunks_reader("d1", only_doc=True, universe=3)
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


def test_rerank_pool_represents_each_doc_before_second_chunks():
    class FloodStore(FakeStore):
        def search(self, vector, limit):
            return [VectorHit(i, 1.0 - i / 10) for i in (1, 2, 3, 4)]

    class Reader(FakeReader):
        def get_chunks(self, ids):
            rows = {
                1: ChunkView(
                    1, "a:1", "docA", "H", "a1", "Doc A", "1.0", "approved", "geopos", "a.md"
                ),
                2: ChunkView(
                    2, "a:2", "docA", "H", "a2", "Doc A", "1.0", "approved", "geopos", "a.md"
                ),
                3: ChunkView(
                    3, "a:3", "docA", "H", "a3", "Doc A", "1.0", "approved", "geopos", "a.md"
                ),
                4: ChunkView(
                    4, "b:1", "docB", "H", "b1", "Doc B", "1.0", "approved", "geopos", "b.md"
                ),
            }
            return [rows[i] for i in ids]

        def lexical_search(self, query, limit, apps=None, statuses=None):
            return []

    class BBestReranker:
        def score(self, query, passages):
            return [0.9 if passage.startswith("H\nb1") else 0.1 for passage in passages]

    # docA inunda el top de fusión; docB es el caso correcto según el reranker.
    retriever = Retriever(Reader(), FloodStore(), FakeEmbedder(), BBestReranker(), rerank_top_n=2)
    result = retriever.retrieve("consulta")
    assert result.chunks[0].doc_id == "docB"


def test_winner_expansion_includes_adjacent_section_chunks():
    class OneStore(FakeStore):
        def search(self, vector, limit):
            return [VectorHit(3, 0.9), VectorHit(9, 0.5)]

    class Reader(FakeReader):
        def _rows(self):
            rows = {
                i: ChunkView(
                    i, f"a:{i}", "docA", "H", f"a{i}", "Doc A", "1.0", "approved", "geopos", "a.md"
                )
                for i in range(1, 6)
            }
            rows[9] = ChunkView(
                9, "z:1", "docZ", "H", "z1", "Doc Z", "1.0", "approved", "geopos", "z.md"
            )
            return rows

        def get_chunks(self, ids):
            rows = self._rows()
            return [rows[i] for i in ids]

        def get_doc_chunks(self, doc_id):
            return [c for c in self._rows().values() if c.doc_id == doc_id]

        def lexical_search(self, query, limit, apps=None, statuses=None):
            return []

    # Solo el chunk 3 del caso es candidato; el contenido útil está en los vecinos.
    retriever = Retriever(Reader(), OneStore(), FakeEmbedder(), None, max_case_chunks=3)
    result = retriever.retrieve("consulta")
    ids = [c.id for c in result.chunks if c.doc_id == "docA"]
    assert ids == [2, 3, 4]


def test_winner_expansion_respects_cap_across_many_chunks():
    class OneStore(FakeStore):
        def search(self, vector, limit):
            return [VectorHit(10, 0.9)]

    def chunk(i):
        return ChunkView(
            i, f"a:{i}", "docA", "H", f"a{i}", "Doc A", "1.0", "approved", "geopos", "a.md"
        )

    class BigReader(FakeReader):
        def get_chunks(self, ids):
            return [chunk(i) for i in ids]

        def get_doc_chunks(self, doc_id):
            return [chunk(i) for i in range(1, 21)]

        def lexical_search(self, query, limit, apps=None, statuses=None):
            return []

    retriever = Retriever(BigReader(), OneStore(), FakeEmbedder(), None, max_case_chunks=4)
    result = retriever.retrieve("consulta")
    ids = [c.id for c in result.chunks]
    assert len(ids) == 4
    assert sorted(ids) == [9, 10, 11, 12]


def test_winner_expansion_prefers_right_neighbors_of_candidates():
    # Cita el chunk 12 ("...con lo siguiente QUERY:") pero el SQL está en el 13.
    class Store(FakeStore):
        def search(self, vector, limit):
            return [VectorHit(3, 0.9), VectorHit(7, 0.8), VectorHit(12, 0.7)]

    def chunk(i):
        return ChunkView(
            i, f"a:{i}", "docA", "H", f"a{i}", "Doc A", "1.0", "approved", "geopos", "a.md"
        )

    class Reader(FakeReader):
        def get_chunks(self, ids):
            return [chunk(i) for i in ids]

        def get_doc_chunks(self, doc_id):
            return [chunk(i) for i in range(1, 21)]

        def lexical_search(self, query, limit, apps=None, statuses=None):
            return []

    retriever = Retriever(Reader(), Store(), FakeEmbedder(), None, max_case_chunks=8)
    result = retriever.retrieve("consulta")
    ids = [c.id for c in result.chunks if c.doc_id == "docA"]
    assert 13 in ids
