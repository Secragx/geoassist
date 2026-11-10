"""Pipeline híbrido de recuperación: denso + léxico + RRF + rerank + abstención."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from geoassist.rag.embeddings import Embedder
from geoassist.rag.fusion import FusedHit, reciprocal_rank_fusion
from geoassist.rag.rerank import Reranker
from geoassist.rag.types import RetrievalResult, RetrievedChunk
from geoassist.rag.vector_store import VectorHit, VectorStore
from geoassist.storage.corpus_read import ChunkView, CorpusReader, LexicalHit


class Retriever:
    """Orquesta la recuperación híbrida con filtros, rerank y abstención."""

    def __init__(
        self,
        reader: CorpusReader,
        store: VectorStore,
        embedder: Embedder,
        reranker: Reranker | None,
        *,
        dense_limit: int = 20,
        lexical_limit: int = 20,
        rerank_top_n: int = 8,
        final_limit: int = 8,
        max_per_doc: int = 3,
        max_case_chunks: int = 8,
        min_score: float = 0.0,
    ) -> None:
        self._reader = reader
        self._store = store
        self._embedder = embedder
        self._reranker = reranker
        self._dense_limit = dense_limit
        self._lexical_limit = lexical_limit
        self._rerank_top_n = rerank_top_n
        self._final_limit = final_limit
        self._max_per_doc = max_per_doc
        self._max_case_chunks = max_case_chunks
        self._min_score = min_score

    def retrieve(
        self,
        question: str,
        app_filter: str | None = None,
        statuses: Sequence[str] = ("approved", "pending"),
    ) -> RetrievalResult:
        """Recupera chunks relevantes para la pregunta."""

        normalized_question = question.strip() if question else ""
        if not normalized_question:
            trace = self._build_trace(
                question=normalized_question,
                app_filter=app_filter,
                statuses=statuses,
            )
            return RetrievalResult(
                chunks=(),
                sufficient=False,
                reason="no_candidates",
                trace=trace,
            )

        query_vector = self._embedder.embed_query(normalized_question)
        apps: Sequence[str] | None = [app_filter] if app_filter else None
        valid_statuses = set(statuses)

        dense_hits = self._store.search(query_vector, self._dense_limit)
        lexical_hits = self._reader.lexical_search(
            normalized_question,
            self._lexical_limit,
            apps=apps,
            statuses=statuses,
        )

        fused = reciprocal_rank_fusion(dense_hits, lexical_hits, k=60)

        chunk_ids = [hit.chunk_row_id for hit in fused]
        chunks_by_id = {chunk.id: chunk for chunk in self._reader.get_chunks(chunk_ids)}

        candidates: list[tuple[FusedHit, ChunkView]] = []
        for hit in fused:
            chunk = chunks_by_id.get(hit.chunk_row_id)
            if chunk is None:
                continue
            if chunk.status not in valid_statuses:
                continue
            if app_filter is not None and chunk.app != app_filter:
                continue
            candidates.append((hit, chunk))

        doc_counts: dict[str, int] = {}
        limited: list[tuple[FusedHit, ChunkView]] = []
        for hit, chunk in candidates:
            count = doc_counts.get(chunk.doc_id, 0)
            if count >= self._max_per_doc:
                continue
            doc_counts[chunk.doc_id] = count + 1
            limited.append((hit, chunk))

        reranked: list[tuple[FusedHit, ChunkView, float | None]] = []
        if self._reranker is not None and limited:
            to_rerank = self._diverse_pool(limited)
            pool_ids = {chunk.id for _, chunk in to_rerank}
            passages = [chunk.heading + "\n" + chunk.text for _, chunk in to_rerank]
            rerank_scores = self._reranker.score(normalized_question, passages)
            scored = [
                (hit, chunk, float(score))
                for (hit, chunk), score in zip(to_rerank, rerank_scores, strict=True)
            ]
            scored.sort(key=lambda item: (-item[2], item[1].id))
            reranked.extend(scored)
            for hit, chunk in limited:
                if chunk.id not in pool_ids:
                    reranked.append((hit, chunk, None))
        else:
            for hit, chunk in limited:
                reranked.append((hit, chunk, None))

        final = reranked[: self._final_limit]
        assembled = self._assemble_case_context(final, candidates)

        retrieved_chunks = tuple(
            RetrievedChunk(
                id=chunk.id,
                chunk_id=chunk.chunk_id,
                doc_id=chunk.doc_id,
                heading=chunk.heading,
                text=chunk.text,
                title=chunk.title,
                version=chunk.version,
                status=chunk.status,
                app=chunk.app,
                rel_path=chunk.rel_path,
                score=hit.score,
                dense_rank=hit.dense_rank,
                lexical_rank=hit.lexical_rank,
                rerank_score=rerank_score,
            )
            for hit, chunk, rerank_score in assembled
        )

        best_score = self._best_score(final)
        sufficient = len(retrieved_chunks) > 0 and (
            best_score is None or best_score >= self._min_score
        )

        trace = self._build_trace(
            question=normalized_question,
            app_filter=app_filter,
            statuses=statuses,
            dense_hits=dense_hits,
            lexical_hits=lexical_hits,
            fused=fused,
            candidates=candidates,
            limited=limited,
            final=final,
        )

        if not retrieved_chunks:
            return RetrievalResult(
                chunks=(),
                sufficient=False,
                reason="no_candidates",
                trace=trace,
            )

        if not sufficient:
            return RetrievalResult(
                chunks=(),
                sufficient=False,
                reason="low_score",
                trace=trace,
            )

        return RetrievalResult(
            chunks=retrieved_chunks,
            sufficient=True,
            reason=None,
            trace=trace,
        )

    def _diverse_pool(
        self,
        limited: list[tuple[FusedHit, ChunkView]],
    ) -> list[tuple[FusedHit, ChunkView]]:
        """Selecciona el pool de rerank cubriendo cada documento antes de repetir.

        Sin esto, los chunks de un documento que inunda el top de fusión
        desplazan al caso correcto fuera del rerank y nunca se puntúan.
        """
        by_doc: dict[str, list[tuple[FusedHit, ChunkView]]] = {}
        doc_order: list[str] = []
        for item in limited:
            doc_id = item[1].doc_id
            if doc_id not in by_doc:
                by_doc[doc_id] = []
                doc_order.append(doc_id)
            by_doc[doc_id].append(item)

        pool: list[tuple[FusedHit, ChunkView]] = []
        round_idx = 0
        while len(pool) < self._rerank_top_n:
            added = False
            for doc_id in doc_order:
                items = by_doc[doc_id]
                if round_idx < len(items):
                    pool.append(items[round_idx])
                    added = True
                    if len(pool) >= self._rerank_top_n:
                        break
            if not added:
                break
            round_idx += 1
        return pool

    def _assemble_case_context(
        self,
        final: list[tuple[FusedHit, ChunkView, float | None]],
        candidates: list[tuple[FusedHit, ChunkView]],
    ) -> list[tuple[FusedHit, ChunkView, float | None]]:
        """Ensambla el contexto a nivel de caso, no de fragmento.

        El documento del caso ganador (top-1 tras rerank) aporta sus fragmentos
        candidatos junto con los chunks vecinos de la misma sección (hasta
        ``max_case_chunks``, en orden de lectura), de modo que el detalle no
        dependa de la frase usada para preguntar. Los demás documentos aportan
        como máximo ``max_per_doc`` fragmentos secundarios.
        """
        if not final:
            return []

        winner_chunks = self._winner_context(final, candidates)
        hit_by_id = {chunk.id: hit for hit, chunk in candidates}
        rerank_by_id = {chunk.id: score for _, chunk, score in final}
        fallback_hit = final[0][0]

        assembled: list[tuple[FusedHit, ChunkView, float | None]] = [
            (hit_by_id.get(chunk.id, fallback_hit), chunk, rerank_by_id.get(chunk.id))
            for chunk in winner_chunks
        ]
        seen = {chunk.id for _, chunk, _ in assembled}

        others = 0
        for hit, chunk, score in final:
            if chunk.id in seen:
                continue
            if others >= self._max_per_doc:
                break
            others += 1
            assembled.append((hit, chunk, score))
            seen.add(chunk.id)
        return assembled

    def _winner_context(
        self,
        final: list[tuple[FusedHit, ChunkView, float | None]],
        candidates: list[tuple[FusedHit, ChunkView]],
    ) -> list[ChunkView]:
        """Chunks del caso ganador: candidatos más vecinos de sección.

        Selecciona primero los fragmentos candidatos y luego los chunks más
        próximos en orden de lectura (la estructura de secciones del chunker
        agrupa juntos pasos, validaciones y bloques relacionados), acotado a
        ``max_case_chunks`` y devuelto en orden de lectura.
        """
        winner_doc_id = final[0][1].doc_id
        candidate_chunks = [chunk for _, chunk in candidates if chunk.doc_id == winner_doc_id]
        candidate_ids = {chunk.id for chunk in candidate_chunks}
        doc_chunks = self._reader.get_doc_chunks(winner_doc_id) or candidate_chunks

        positions = {chunk.id: idx for idx, chunk in enumerate(doc_chunks)}
        candidate_positions = [positions[cid] for cid in candidate_ids if cid in positions]

        def rank_key(chunk: ChunkView) -> tuple[int, int, int]:
            pos = positions.get(chunk.id, 0)
            if chunk.id in candidate_ids:
                return (0, 0, pos)
            # Vecinos derechos primero: la continuidad de un procedimiento
            # (p. ej. "con lo siguiente QUERY:" y su bloque SQL) va hacia adelante.
            nearest = min(candidate_positions, key=lambda cp: (abs(pos - cp), -cp))
            is_left = 1 if pos < nearest else 0
            return (abs(pos - nearest), is_left, pos)

        chosen = sorted(doc_chunks, key=rank_key)[: self._max_case_chunks]
        chosen.sort(key=lambda chunk: chunk.id)
        return chosen

    def _best_score(
        self,
        final: Sequence[tuple[FusedHit, ChunkView, float | None]],
    ) -> float | None:
        """Devuelve el mejor score final (rerank si existe, RRF en caso contrario)."""

        if not final:
            return None

        if final[0][2] is not None:
            return max(item[2] for item in final if item[2] is not None)

        return max(item[0].score for item in final)

    def _build_trace(
        self,
        *,
        question: str,
        app_filter: str | None,
        statuses: Sequence[str],
        dense_hits: Sequence[VectorHit] | None = None,
        lexical_hits: Sequence[LexicalHit] | None = None,
        fused: Sequence[FusedHit] | None = None,
        candidates: Sequence[tuple[FusedHit, ChunkView]] | None = None,
        limited: Sequence[tuple[FusedHit, ChunkView]] | None = None,
        final: Sequence[tuple[FusedHit, ChunkView, float | None]] | None = None,
    ) -> dict[str, Any]:
        """Construye la traza de la consulta sin incluir textos completos."""

        trace: dict[str, Any] = {
            "question": question,
            "app_filter": app_filter,
            "statuses": list(statuses),
            "dense_limit": self._dense_limit,
            "lexical_limit": self._lexical_limit,
            "rerank_top_n": self._rerank_top_n,
            "final_limit": self._final_limit,
            "max_per_doc": self._max_per_doc,
            "min_score": self._min_score,
        }

        if dense_hits is not None:
            trace["dense_count"] = len(dense_hits)
        if lexical_hits is not None:
            trace["lexical_count"] = len(lexical_hits)
        if fused is not None:
            trace["fused_count"] = len(fused)
        if candidates is not None:
            trace["filtered_count"] = len(candidates)
        if limited is not None:
            trace["limited_count"] = len(limited)
        if final is not None:
            trace["final_count"] = len(final)
            trace["chunks"] = [
                {
                    "id": chunk.id,
                    "doc_id": chunk.doc_id,
                    "chunk_id": chunk.chunk_id,
                    "title": chunk.title,
                    "status": chunk.status,
                    "app": chunk.app,
                    "rel_path": chunk.rel_path,
                    "score": hit.score,
                    "dense_rank": hit.dense_rank,
                    "lexical_rank": hit.lexical_rank,
                    "rerank_score": rerank_score,
                }
                for hit, chunk, rerank_score in final
            ]

        return trace
