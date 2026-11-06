import pytest
from geoassist.rag.fusion import reciprocal_rank_fusion
from geoassist.rag.vector_store import VectorHit
from geoassist.storage.corpus_read import LexicalHit


def test_scores_sum_reciprocal_ranks_and_dedupe():
    dense = [VectorHit(1, 0.9), VectorHit(2, 0.8)]
    lexical = [LexicalHit(2, 5.0), LexicalHit(3, 4.0)]
    fused = reciprocal_rank_fusion(dense, lexical, k=60)
    by_id = {hit.chunk_row_id: hit for hit in fused}
    assert by_id[2].score == pytest.approx(1 / 62 + 1 / 61)
    assert by_id[1].score == pytest.approx(1 / 61)
    assert [hit.chunk_row_id for hit in fused] == [2, 1, 3]


def test_empty_lists_are_safe():
    assert reciprocal_rank_fusion([], [], k=60) == []
