"""Métricas y estructuras del set dorado de evaluación."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class GoldenCase:
    """Caso etiquetado del set dorado de evaluación."""

    id: str = ""
    question: str = ""
    app: str | None = None
    expected_docs: list[str] = field(default_factory=list)
    expected_facts: list[str] = field(default_factory=list)
    must_abstain: bool = False
    notes: str = ""


@dataclass(frozen=True, slots=True)
class EvalMetrics:
    """Métricas agregadas de una corrida de evaluación."""

    recall_at_5: float = 0.0
    mrr_at_10: float = 0.0
    ndcg_at_10: float = 0.0
    app_accuracy: float = 0.0
    abstention_accuracy: float = 0.0


def recall_at_k(ranked_docs: Sequence[Any], expected_docs: Sequence[Any], k: int) -> float:
    """Proporción de documentos esperados presentes en el top-``k``."""

    if k <= 0:
        return 0.0
    expected = set(expected_docs)
    if not expected:
        return 1.0
    top = set(list(ranked_docs)[:k])
    hits = sum(1 for doc in expected if doc in top)
    return hits / len(expected)


def mean_reciprocal_rank(
    ranked_docs: Sequence[Any], expected_docs: Sequence[Any], k: int = 10
) -> float:
    """Recíproco del rango del primer documento esperado en el top-``k``."""

    if k <= 0:
        return 0.0
    expected = set(expected_docs)
    if not expected:
        return 0.0
    for index, doc in enumerate(list(ranked_docs)[:k], start=1):
        if doc in expected:
            return 1.0 / index
    return 0.0


def ndcg_at_k(ranked_docs: Sequence[Any], expected_docs: Sequence[Any], k: int = 10) -> float:
    """nDCG@k con ganancia unitaria por documento esperado."""

    if k <= 0:
        return 0.0
    expected = set(expected_docs)
    if not expected:
        return 0.0
    dcg = 0.0
    for index, doc in enumerate(list(ranked_docs)[:k], start=1):
        if doc in expected:
            dcg += 1.0 / math.log2(index + 1)
    ideal_hits = min(len(expected), k)
    idcg = sum(1.0 / math.log2(index + 1) for index in range(1, ideal_hits + 1))
    if idcg == 0:
        return 0.0
    return dcg / idcg


def _get(case: Any, key: str) -> Any:
    if isinstance(case, Mapping):
        return case.get(key)
    if hasattr(case, key):
        return getattr(case, key)
    return None


def _attr(chunk: Any, key: str) -> Any:
    if isinstance(chunk, Mapping):
        return chunk.get(key)
    return getattr(chunk, key, None)


def evaluate(golden: Iterable[Any], retriever: Any) -> EvalMetrics:
    """Evalúa un set dorado contra un recuperador y devuelve métricas agregadas."""

    cases = list(golden)
    recalls: list[float] = []
    mrrs: list[float] = []
    ndcgs: list[float] = []
    apps: list[float] = []
    abstains: list[float] = []

    for case in cases:
        question = _get(case, "question") or ""
        app = _get(case, "app")
        expected_docs = _get(case, "expected_docs") or []
        must_abstain = bool(_get(case, "must_abstain"))

        result = retriever.retrieve(question, app_filter=app)

        chunks = getattr(result, "chunks", None)
        if chunks is None and isinstance(result, Mapping):
            chunks = result.get("chunks", [])
        chunks = list(chunks or [])

        sufficient = getattr(result, "sufficient", None)
        if sufficient is None and isinstance(result, Mapping):
            sufficient = result.get("sufficient", False)

        ranked: list[Any] = []
        seen: set[Any] = set()
        for chunk in chunks:
            rel_path = _attr(chunk, "rel_path")
            if rel_path is None:
                continue
            if rel_path in seen:
                continue
            seen.add(rel_path)
            ranked.append(rel_path)

        recalls.append(recall_at_k(ranked, expected_docs, 5))
        mrrs.append(mean_reciprocal_rank(ranked, expected_docs, 10))
        ndcgs.append(ndcg_at_k(ranked, expected_docs, 10))

        if chunks:
            if app not in (None, "", "unknown"):
                ok = all(_attr(chunk, "app") == app for chunk in chunks)
            else:
                ok = True
        else:
            ok = bool(must_abstain)
        apps.append(1.0 if ok else 0.0)

        abstains.append(1.0 if must_abstain == (not bool(sufficient)) else 0.0)

    def _mean(values: list[float]) -> float:
        if not values:
            return 0.0
        return sum(values) / len(values)

    return EvalMetrics(
        recall_at_5=_mean(recalls),
        mrr_at_10=_mean(mrrs),
        ndcg_at_10=_mean(ndcgs),
        app_accuracy=_mean(apps),
        abstention_accuracy=_mean(abstains),
    )
