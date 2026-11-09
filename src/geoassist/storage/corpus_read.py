"""Lectura de solo lectura de una versión publicada del corpus."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from geoassist.ingest.rules import normalize_text
from geoassist.rag.lexical import build_fts_query

_VOCAB_STOPWORDS = frozenset(
    "de la el los las para por con una un que del al como mas esta este "
    "desde sobre entre cuando puede debe no si o y e u a".split()
)
_VOCAB_MIN_LEN = 4
_VOCAB_MAX_TERMS = 2000


@dataclass(frozen=True, slots=True)
class ChunkView:
    """Vista combinada de un chunk y los metadatos de su documento."""

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


@dataclass(frozen=True, slots=True)
class LexicalHit:
    """Coincidencia de una búsqueda FTS5: fila del chunk y puntuación BM25."""

    chunk_row_id: int
    score: float


class CorpusReader:
    """Acceso de solo lectura al SQLite de una versión del corpus."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        if not self._db_path.is_file():
            raise FileNotFoundError(f"no existe la base de datos del corpus: {self._db_path}")
        self._uri = f"file:{self._db_path}?mode=ro"

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._uri, uri=True)

    def lexical_search(
        self,
        query: str,
        limit: int,
        apps: Sequence[str] | None = None,
        statuses: Sequence[str] = ("approved", "pending"),
    ) -> list[LexicalHit]:
        """Busca chunks con FTS5 usando BM25 y filtra por app y status."""

        fts_query = build_fts_query(query)
        if not fts_query or limit <= 0:
            return []

        params: list[str | int] = [fts_query]
        filters: list[str] = []

        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            filters.append(f"documents.status IN ({placeholders})")
            params.extend(statuses)

        if apps:
            placeholders = ", ".join("?" for _ in apps)
            filters.append(f"documents.app IN ({placeholders})")
            params.extend(apps)

        where_clause = " AND ".join(filters)
        if where_clause:
            where_clause = " AND " + where_clause

        sql = f"""
            SELECT chunks.id, bm25(chunks_fts, 3.0, 1.0) AS score
            FROM chunks_fts
            JOIN chunks ON chunks.rowid = chunks_fts.rowid
            JOIN documents ON documents.doc_id = chunks.doc_id
            WHERE chunks_fts MATCH ?{where_clause}
            ORDER BY score ASC
            LIMIT ?
        """
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [LexicalHit(chunk_row_id=int(row[0]), score=float(row[1])) for row in rows]

    def get_chunks(self, ids: Sequence[int]) -> list[ChunkView]:
        """Devuelve los chunks indicados preservando el orden solicitado."""

        if not ids:
            return []

        placeholders = ", ".join("?" for _ in ids)
        sql = f"""
            SELECT
                chunks.id,
                chunks.chunk_id,
                chunks.doc_id,
                chunks.heading,
                chunks.text,
                documents.title,
                documents.version,
                documents.status,
                documents.app,
                documents.rel_path
            FROM chunks
            JOIN documents ON documents.doc_id = chunks.doc_id
            WHERE chunks.id IN ({placeholders})
        """

        with self._connect() as conn:
            rows = conn.execute(sql, list(ids)).fetchall()

        by_id: dict[int, ChunkView] = {}
        for row in rows:
            view = ChunkView(
                id=int(row[0]),
                chunk_id=row[1],
                doc_id=row[2],
                heading=row[3] or "",
                text=row[4],
                title=row[5],
                version=row[6],
                status=row[7],
                app=row[8],
                rel_path=row[9],
            )
            by_id[view.id] = view

        return [by_id[id] for id in ids if id in by_id]

    def counts(self) -> dict[str, int]:
        """Devuelve el número de documentos y chunks de la versión."""

        with self._connect() as conn:
            doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return {"documents": int(doc_count), "chunks": int(chunk_count)}

    def vocabulary(self) -> list[str]:
        """Términos de títulos y headings para corrección local de consultas.

        Normalizados (minúsculas, sin acentos), sin stopwords ni términos
        cortos, deduplicados, ordenados por frecuencia descendente y acotados
        a 2000.
        """
        counts: dict[str, int] = {}
        with self._connect() as conn:
            rows = conn.execute("SELECT title FROM documents").fetchall()
            rows += conn.execute("SELECT heading FROM chunks").fetchall()
        for row in rows:
            for token in re.findall(r"\w+", str(row[0] or "")):
                term = normalize_text(token)
                if len(term) < _VOCAB_MIN_LEN or term in _VOCAB_STOPWORDS:
                    continue
                counts[term] = counts.get(term, 0) + 1
        ordered = sorted(counts, key=lambda term: (-counts[term], term))
        return ordered[:_VOCAB_MAX_TERMS]
