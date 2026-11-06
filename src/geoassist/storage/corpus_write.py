"""Escritura atómica del corpus SQLite a partir de filas de documentos y chunks."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from geoassist.storage.migrations import apply_migrations

_MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations" / "corpus"


@dataclass(frozen=True, slots=True)
class DocumentRow:
    """Fila de la tabla ``documents`` del corpus."""

    doc_id: str
    rel_path: str
    title: str
    app: str
    status: str
    version: str | None
    content_hash: str
    frontmatter_json: str
    chunk_count: int
    indexed_at: str


@dataclass(frozen=True, slots=True)
class ChunkRow:
    """Fila de la tabla ``chunks`` del corpus."""

    chunk_id: str
    doc_id: str
    ordinal: int
    heading: str
    text: str
    token_count: int
    has_images: bool


def _utc_now() -> str:
    """Devuelve la fecha actual como ISO-8601 UTC con sufijo ``Z``."""

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def write_corpus(
    db_path: Path,
    documents: Sequence[DocumentRow],
    chunks: Sequence[ChunkRow],
    migrations_dir: Path | None = None,
) -> None:
    """Crea ``db_path``, aplica migraciones, inserta filas y puebla ``chunks_fts``."""

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    migrations_path = Path(migrations_dir) if migrations_dir is not None else _MIGRATIONS_DIR
    apply_migrations(db_path, migrations_path)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")

        conn.executemany(
            """
            INSERT INTO documents (
                doc_id, rel_path, title, app, status, version,
                content_hash, frontmatter, chunk_count, indexed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    document.doc_id,
                    document.rel_path,
                    document.title,
                    document.app,
                    document.status,
                    document.version,
                    document.content_hash,
                    document.frontmatter_json,
                    document.chunk_count,
                    document.indexed_at,
                )
                for document in documents
            ],
        )

        conn.executemany(
            """
            INSERT INTO chunks (
                chunk_id, doc_id, ordinal, heading, text, token_count, has_images
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    chunk.chunk_id,
                    chunk.doc_id,
                    chunk.ordinal,
                    chunk.heading,
                    chunk.text,
                    chunk.token_count,
                    int(chunk.has_images),
                )
                for chunk in chunks
            ],
        )

        conn.execute(
            "INSERT INTO chunks_fts(rowid, heading, text) SELECT id, heading, text FROM chunks"
        )

        conn.commit()
    finally:
        conn.close()
