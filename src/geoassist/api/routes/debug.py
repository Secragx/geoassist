"""Rutas de diagnóstico interno protegidas por token."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel

from geoassist.api.app import AppDependencies
from geoassist.rag.types import RetrievedChunk

router = APIRouter(tags=["debug"])


class RetrievalDebugRequest(BaseModel):
    """Cuerpo de la consulta de diagnóstico de recuperación."""

    question: str


def _get_dependencies(request: Request) -> AppDependencies:
    return request.app.state.dependencies


def _chunk_summary(chunk: RetrievedChunk) -> dict[str, Any]:
    """Metadatos de un chunk sin el texto completo."""

    return {
        "id": chunk.id,
        "chunk_id": chunk.chunk_id,
        "doc_id": chunk.doc_id,
        "heading": chunk.heading,
        "title": chunk.title,
        "version": chunk.version,
        "status": chunk.status,
        "app": chunk.app,
        "rel_path": chunk.rel_path,
        "score": chunk.score,
        "dense_rank": chunk.dense_rank,
        "lexical_rank": chunk.lexical_rank,
        "rerank_score": chunk.rerank_score,
    }


@router.post("/internal/retrieval")
async def debug_retrieval(
    request: RetrievalDebugRequest,
    x_debug_token: str = Header(default=""),
    deps: AppDependencies = Depends(_get_dependencies),  # noqa: B008
) -> dict[str, Any]:
    """Ejecuta recuperación y devuelve traza y chunks resumidos."""

    if not deps.settings.debug_retrieval:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Diagnóstico desactivado")

    expected = deps.settings.debug_token.get_secret_value()
    if not expected or x_debug_token != expected:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token de depuración inválido")

    if deps.retriever is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Recuperador no disponible")

    result = deps.retriever.retrieve(request.question)
    return {
        "trace": result.trace,
        "chunks": [_chunk_summary(chunk) for chunk in result.chunks],
    }
