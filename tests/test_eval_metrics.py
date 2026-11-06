import pytest
from geoassist.eval.metrics import mean_reciprocal_rank, ndcg_at_k, recall_at_k


def test_recall_at_k():
    assert recall_at_k(["a", "b", "c"], ["b"], 5) == 1.0
    assert recall_at_k(["a", "b", "c"], ["z"], 5) == 0.0


def test_mrr():
    assert mean_reciprocal_rank(["a", "b", "c"], ["b"], 10) == pytest.approx(0.5)
    assert mean_reciprocal_rank(["a"], ["z"], 10) == 0.0


def test_ndcg_rewards_early_hits():
    early = ndcg_at_k(["a", "b"], ["a"], 10)
    late = ndcg_at_k(["b", "a"], ["a"], 10)
    assert early == 1.0
    assert late < early
