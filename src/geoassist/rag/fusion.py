"""Fusión de resultados densos y léxicos mediante Reciprocal Rank Fusion."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from geoassist.rag.vector_store import VectorHit
from geoassist.storage.corpus_read import LexicalHit


@dataclass(frozen=True, slots=True)
class FusedHit:
    """Resultado fusionado: identificador, puntuación RRF y ranks originales."""

    chunk_row_id: int
    score: float
    dense_rank: int | None
    lexical_rank: int | None


def reciprocal_rank_fusion(
    dense: Sequence[VectorHit],
    lexical: Sequence[LexicalHit],
    k: int = 60,
) -> list[FusedHit]:
    """Combina listas ordenadas de hits usando RRF con k=60 por defecto.

    Cada lista aporta ``1 / (k + rank)`` usando rank 1-based. Los resultados se
    desduplican por ``chunk_row_id``, se ordenan por score descendente y, en
    caso de empate, por ``chunk_row_id`` ascendente para determinismo.
    """

    scores: dict[int, float] = {}
    dense_ranks: dict[int, int] = {}
    lexical_ranks: dict[int, int] = {}

    for rank, dense_hit in enumerate(dense, start=1):
        chunk_id = dense_hit.chunk_row_id
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
        if chunk_id not in dense_ranks:
            dense_ranks[chunk_id] = rank

    for rank, lexical_hit in enumerate(lexical, start=1):
        chunk_id = lexical_hit.chunk_row_id
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
        if chunk_id not in lexical_ranks:
            lexical_ranks[chunk_id] = rank

    fused = [
        FusedHit(
            chunk_row_id=chunk_id,
            score=score,
            dense_rank=dense_ranks.get(chunk_id),
            lexical_rank=lexical_ranks.get(chunk_id),
        )
        for chunk_id, score in scores.items()
    ]
    fused.sort(key=lambda hit: (-hit.score, hit.chunk_row_id))
    return fused
