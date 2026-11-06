"""Pipeline de ingesta que construye una versión inmutable del corpus."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from geoassist.ingest.chunker import Chunker
from geoassist.ingest.loader import ManualLoader, extract_first_h1
from geoassist.ingest.metadata import MetadataResolver
from geoassist.storage.corpus_write import ChunkRow, DocumentRow, write_corpus

if TYPE_CHECKING:
    from geoassist.rag.embeddings import Embedder
    from geoassist.rag.tokenization import TokenCounter


@dataclass(frozen=True, slots=True)
class SkippedRecord:
    """Archivo Markdown descubierto pero ignorado por las reglas del corpus."""

    rel_path: str
    reason: str


@dataclass(frozen=True, slots=True)
class IngestReport:
    """Resultado de una ejecución completa del pipeline de ingesta."""

    version_id: str
    doc_count: int
    chunk_count: int
    warnings: tuple[str, ...]
    skipped: tuple[SkippedRecord, ...]


class IngestValidationError(ValueError):
    """El corpus generado no supera las validaciones de conteo, dimensión o integridad."""


class Ingestor:
    """Orquesta la carga, chunking, embebido y escritura de una versión del corpus."""

    def __init__(
        self,
        loader: ManualLoader,
        chunker: Chunker,
        resolver: MetadataResolver,
        embedder: Embedder,
        counter: TokenCounter,
        migrations_dir: Path | None = None,
    ) -> None:
        self._loader = loader
        self._chunker = chunker
        self._resolver = resolver
        self._embedder = embedder
        self._counter = counter
        self._migrations_dir = (
            Path(migrations_dir)
            if migrations_dir is not None
            else Path(loader.repo_root) / "migrations" / "corpus"
        )

    def build(self, version_id: str, output_dir: Path) -> IngestReport:
        """Construye el corpus en un directorio temporal y lo publica atómicamente."""

        output_dir = Path(output_dir)
        work_dir = Path(
            tempfile.mkdtemp(
                dir=output_dir.parent,
                prefix=f".ingest-{version_id}-",
            )
        )

        try:
            report = self._build_in(work_dir, version_id)
            os.replace(work_dir, output_dir)
            return report
        except BaseException:
            if work_dir.exists():
                shutil.rmtree(work_dir)
            raise

    def _build_in(self, work_dir: Path, version_id: str) -> IngestReport:
        db_path = work_dir / "corpus.db"
        index_path = work_dir / "faiss.index"
        manifest_path = work_dir / "manifest.json"

        raw_documents = self._loader.discover()
        skipped = self._discover_skipped()

        documents: list[DocumentRow] = []
        chunks: list[ChunkRow] = []
        warnings: list[str] = []
        doc_ids: set[str] = set()
        indexed_at = _utc_now()

        for raw in raw_documents:
            doc_id = _stable_id(raw.rel_path)
            if doc_id in doc_ids:
                raise IngestValidationError(f"doc_id duplicado: {doc_id} ({raw.rel_path})")
            doc_ids.add(doc_id)

            first_h1 = extract_first_h1(raw.text)
            metadata = self._resolver.resolve(raw.rel_path, raw.frontmatter, first_h1)

            warnings.extend(raw.warnings)
            if metadata.app == "unknown":
                warnings.append(f"aplicación desconocida: {raw.rel_path}")
            if metadata.status == "unknown":
                warnings.append(f"estado desconocido: {raw.rel_path}")

            text_chunks = self._chunker.chunk(raw.text, title=metadata.title)

            documents.append(
                DocumentRow(
                    doc_id=doc_id,
                    rel_path=raw.rel_path,
                    title=metadata.title,
                    app=metadata.app,
                    status=metadata.status,
                    version=metadata.version,
                    content_hash=raw.content_hash,
                    frontmatter_json=json.dumps(
                        metadata.frontmatter, ensure_ascii=False, default=str
                    ),
                    chunk_count=len(text_chunks),
                    indexed_at=indexed_at,
                )
            )

            for text_chunk in text_chunks:
                chunks.append(
                    ChunkRow(
                        chunk_id=f"{doc_id}:{text_chunk.ordinal}",
                        doc_id=doc_id,
                        ordinal=text_chunk.ordinal,
                        heading=text_chunk.heading,
                        text=text_chunk.text,
                        token_count=text_chunk.token_count,
                        has_images=text_chunk.has_images,
                    )
                )

        orphan_chunks = [chunk.chunk_id for chunk in chunks if chunk.doc_id not in doc_ids]
        if orphan_chunks:
            raise IngestValidationError(f"chunks huérfanos: {orphan_chunks[:5]}")

        write_corpus(db_path, documents, chunks, migrations_dir=self._migrations_dir)

        chunk_row_ids, chunk_texts = self._read_chunk_texts(db_path)
        if len(chunk_row_ids) != len(chunks):
            raise IngestValidationError(
                f"conteo de chunks inconsistente: {len(chunk_row_ids)} en BD "
                f"vs {len(chunks)} generados"
            )

        vectors = self._embedder.embed_documents(chunk_texts) if chunk_texts else []

        if len(vectors) != len(chunk_row_ids):
            raise IngestValidationError(
                f"conteo de vectores inconsistente: {len(vectors)} vectores "
                f"vs {len(chunk_row_ids)} chunks"
            )

        dimension = self._embedder.dimension
        for index, vector in enumerate(vectors):
            if len(vector) != dimension:
                raise IngestValidationError(
                    f"dimensión incorrecta en chunk {chunk_row_ids[index]}: "
                    f"se esperaban {dimension}, se obtuvieron {len(vector)}"
                )

        from geoassist.rag.vector_store import VectorStore

        VectorStore.build(index_path, np.asarray(vectors, dtype=np.float32), ids=chunk_row_ids)

        _write_corpus_meta(
            db_path,
            version_id=version_id,
            doc_count=len(documents),
            chunk_count=len(chunks),
            dimension=dimension,
            embedding_model=type(self._embedder).__name__,
        )

        db_sha256 = _sha256(db_path)
        index_sha256 = _sha256(index_path)

        if not db_path.is_file() or not index_path.is_file():
            raise IngestValidationError("faltan archivos de corpus tras la construcción")

        manifest = {
            "version_id": version_id,
            "doc_count": len(documents),
            "chunk_count": len(chunks),
            "dimension": dimension,
            "embedding_model": type(self._embedder).__name__,
            "db_sha256": db_sha256,
            "index_sha256": index_sha256,
            "built_at": _utc_now(),
            "git_commit": version_id,
        }
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        return IngestReport(
            version_id=version_id,
            doc_count=len(documents),
            chunk_count=len(chunks),
            warnings=tuple(warnings),
            skipped=tuple(skipped),
        )

    def _read_chunk_texts(self, db_path: Path) -> tuple[list[int], list[str]]:
        """Lee los identificadores y textos a embeber desde la BD recién escrita."""

        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute("SELECT id, heading, text FROM chunks ORDER BY id").fetchall()
        finally:
            conn.close()

        ids = [row[0] for row in rows]
        texts = [f"{row[1]}\n{row[2]}" for row in rows]
        return ids, texts

    def _discover_skipped(self) -> list[SkippedRecord]:
        """Devuelve los archivos Markdown ignorados por las reglas del corpus."""

        return [
            SkippedRecord(rel_path=rel_path, reason=reason)
            for rel_path, reason in self._loader.discover_ignored()
        ]


def _stable_id(rel_path: str) -> str:
    """Identificador estable de un documento a partir de su ruta relativa."""

    return hashlib.sha256(rel_path.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    """Devuelve la fecha actual como ISO-8601 UTC con sufijo ``Z``."""

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    """Calcula el hash SHA-256 del archivo indicado."""

    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(8192), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _write_corpus_meta(
    db_path: Path,
    *,
    version_id: str,
    doc_count: int,
    chunk_count: int,
    dimension: int,
    embedding_model: str,
) -> None:
    """Persiste los metadatos de la versión en ``corpus_meta``."""

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        entries = [
            ("version_id", version_id),
            ("git_commit", version_id),
            ("built_at", _utc_now()),
            ("doc_count", str(doc_count)),
            ("chunk_count", str(chunk_count)),
            ("embedding_model", embedding_model),
            ("dimension", str(dimension)),
        ]
        conn.executemany(
            "INSERT OR REPLACE INTO corpus_meta (key, value) VALUES (?, ?)",
            entries,
        )
        conn.commit()
    finally:
        conn.close()
