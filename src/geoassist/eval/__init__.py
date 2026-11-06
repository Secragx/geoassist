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
from geoassist.eval.runner import (
    EvaluationReport,
    EvaluationResult,
    EvaluationRunner,
)

__all__ = [
    "EvalMetrics",
    "GoldenCase",
    "evaluate",
    "mean_reciprocal_rank",
    "ndcg_at_k",
    "recall_at_k",
    "EvaluationReport",
    "EvaluationResult",
    "EvaluationRunner",
]
