"""Evaluación del pipeline RAG de GeoAssist."""

from __future__ import annotations

from geoassist.eval.metrics import (
    EvalMetrics,
    GoldenCase,
    evaluate,
    mean_reciprocal_rank,
    ndcg_at_k,
    recall_at_k,
)

__all__ = [
    "EvalMetrics",
    "GoldenCase",
    "evaluate",
    "mean_reciprocal_rank",
    "ndcg_at_k",
    "recall_at_k",
]
