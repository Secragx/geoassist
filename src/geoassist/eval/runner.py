"""Runner de evaluación por dataset JSONL."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from geoassist.rag.retriever import RetrievalResult


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """Resultado de evaluar una pregunta individual."""

    question: str
    expected: str
    actual: str
    passed: bool
    score: float
    latency_ms: int


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Reporte agregado de una corrida de evaluación."""

    total: int
    passed: int
    failed: int
    errors: int
    metrics: dict[str, Any]
    results: list[EvaluationResult] = field(default_factory=list)


class _DefaultRetriever:
    def retrieve(self, question: str, *, app_filter: str | None = None) -> RetrievalResult:
        return RetrievalResult((), False, "no_candidates", {})


class _DefaultGenerator:
    def generate(self, question: str, chunks: Any, **kwargs: Any) -> Any:
        class _R:
            answer = ""
            sources: list[Any] = []

        return _R()

    def complete(self, prompt: str, *, max_tokens: int = 0, temperature: float = 0.0) -> str:
        return ""


class EvaluationRunner:
    """Ejecuta una evaluación sobre un dataset JSONL."""

    def __init__(
        self,
        retriever: Any = None,
        generator: Any = None,
        *,
        llm: Any = None,
        clock: Callable[[], float] = time.monotonic,
        **kwargs: Any,
    ) -> None:
        self._retriever = retriever if retriever is not None else _DefaultRetriever()
        if generator is not None:
            self._generator = generator
        elif llm is not None:
            self._generator = llm
        else:
            self._generator = _DefaultGenerator()
        self._clock = clock

    def run(
        self,
        dataset_path: Any,
        *,
        top_k: int = 5,
        threshold: float = 0.15,
    ) -> EvaluationReport:
        path = Path(dataset_path)
        cases: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                cases.append(json.loads(line))

        results: list[EvaluationResult] = []
        recalls: list[float] = []
        mrrs: list[float] = []
        passed_count = 0
        failed_count = 0
        errors_count = 0
        latencies: list[int] = []
        abstained_count = 0

        for case in cases:
            question = str(case.get("question", ""))
            expected_answer = str(case.get("expected_answer", ""))
            expected_facts = list(case.get("expected_facts") or [])
            expected = expected_answer or ", ".join(str(f) for f in expected_facts)
            expected_sources = list(case.get("expected_sources") or case.get("expected_docs") or [])
            app_hint = case.get("app_hint", case.get("app"))
            must_abstain = bool(case.get("must_abstain", False))

            start = self._clock()
            try:
                result = self._retriever.retrieve(question, app_filter=app_hint)
            except Exception:  # noqa: BLE001
                errors_count += 1
                latency = int((self._clock() - start) * 1000)
                latencies.append(latency)
                results.append(
                    EvaluationResult(
                        question=question,
                        expected=expected,
                        actual="",
                        passed=False,
                        score=0.0,
                        latency_ms=latency,
                    )
                )
                failed_count += 1
                continue

            sufficient = getattr(result, "sufficient", None)
            if sufficient is None and isinstance(result, Mapping):
                sufficient = result.get("sufficient", False)
            sufficient = bool(sufficient)

            chunks = getattr(result, "chunks", None)
            if chunks is None and isinstance(result, Mapping):
                chunks = result.get("chunks", [])
            chunks = list(chunks or [])

            ranked: list[Any] = []
            seen: set[Any] = set()
            for chunk in chunks:
                if isinstance(chunk, Mapping):
                    rel = chunk.get("rel_path")
                else:
                    rel = getattr(chunk, "rel_path", None)
                if rel is None:
                    continue
                if rel in seen:
                    continue
                seen.add(rel)
                ranked.append(rel)

            # recall@k
            if expected_sources:
                top = set(ranked[:top_k])
                recall = 1.0 if any(src in top for src in expected_sources) else 0.0
            else:
                recall = 1.0
            recalls.append(recall)

            # mrr
            mrr = 0.0
            for idx, doc in enumerate(ranked[:top_k], start=1):
                if doc in expected_sources:
                    mrr = 1.0 / idx
                    break
            mrrs.append(mrr)

            # generate answer
            actual = ""
            if sufficient:
                try:
                    gen_result = self._generator.generate(question, chunks, app_hint=app_hint)
                    raw = getattr(gen_result, "answer", None)
                    if raw is None and isinstance(gen_result, Mapping):
                        raw = gen_result.get("answer", "")
                    if raw is None:
                        raw = str(gen_result)
                    actual = str(raw) if raw is not None else ""
                except Exception:  # noqa: BLE001
                    try:
                        actual = self._generator.complete(question)
                    except Exception:  # noqa: BLE001
                        actual = ""
            else:
                actual = ""

            if not sufficient:
                abstained_count += 1

            # passed: substring match (case-insensitive) if expected_answer present
            if must_abstain:
                passed = not sufficient
            elif expected_answer:
                passed = expected_answer.lower() in (actual or "").lower()
            elif expected_facts:
                low = (actual or "").lower()
                passed = all(str(f).lower() in low for f in expected_facts)
            else:
                passed = sufficient

            if passed:
                passed_count += 1
            else:
                failed_count += 1

            latency = int((self._clock() - start) * 1000)
            latencies.append(latency)
            results.append(
                EvaluationResult(
                    question=question,
                    expected=expected,
                    actual=actual or "",
                    passed=passed,
                    score=mrr,
                    latency_ms=latency,
                )
            )

        total = len(cases)
        metrics = {
            "recall_at_k": (sum(recalls) / len(recalls)) if recalls else 0.0,
            "mrr": (sum(mrrs) / len(mrrs)) if mrrs else 0.0,
            "answer_accuracy": (passed_count / total) if total else 0.0,
            "abstention_rate": (abstained_count / total) if total else 0.0,
            "avg_latency_ms": (sum(latencies) / len(latencies)) if latencies else 0.0,
        }

        return EvaluationReport(
            total=total,
            passed=passed_count,
            failed=failed_count,
            errors=errors_count,
            metrics=metrics,
            results=results,
        )
