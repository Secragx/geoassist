"""Tipos compartidos del módulo RAG para evitar imports circulares."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from geoassist.rag.vector_store import VectorHit
from geoassist.storage.corpus_read import LexicalHit


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """Chunk recuperado con metadatos, ranks originales y scores de cada etapa."""

    id: int
    chunk_id: str
    doc_id: str
    heading: str
    text: str
    title: str
    version: str | None
    status: str
    app: str
    rel_path: str
    score: float
    dense_rank: int | None
    lexical_rank: int | None
    rerank_score: float | None


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """Resultado de una consulta de recuperación híbrida."""

    chunks: tuple[RetrievedChunk, ...]
    sufficient: bool
    reason: str | None
    trace: dict[str, Any] = field(default_factory=dict)


__all__ = ["LexicalHit", "RetrievalResult", "RetrievedChunk", "VectorHit"]
