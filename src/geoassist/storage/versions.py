"""Publicación atómica, gestión de versiones y rollback del corpus."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


class CorpusIntegrityError(RuntimeError):
    """El corpus no supera la verificación de integridad."""


@dataclass(frozen=True, slots=True)
class CorpusVersion:
    """Versión publicada del corpus con sus rutas y checksums."""

    version_id: str
    git_commit: str
    db_path: Path
    index_path: Path
    db_sha256: str
    index_sha256: str
    doc_count: int
    chunk_count: int
    activated_at: str | None


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


def _fsync_file(path: Path) -> None:
    """Fuerza la escritura de un archivo a disco."""

    fd = os.open(str(path), os.O_RDWR)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    """Fuerza la escritura de un directorio a disco."""

    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _verify_files(db_path: Path, index_path: Path, manifest: dict) -> None:
    """Verifica que los archivos existan y coincidan con el manifiesto."""

    if not db_path.is_file() or not index_path.is_file():
        raise CorpusIntegrityError("faltan archivos de corpus")

    db_sha256 = _sha256(db_path)
    index_sha256 = _sha256(index_path)

    if db_sha256 != manifest.get("db_sha256") or index_sha256 != manifest.get("index_sha256"):
        raise CorpusIntegrityError("checksum mismatch")


def _active_version_id(corpus_dir: Path) -> str | None:
    """Devuelve la versión activa según ``current.json`` si existe."""

    version = read_current(corpus_dir)
    return version.version_id if version is not None else None


def _prune_versions(versions_dir: Path, active_id: str | None) -> None:
    """Conserva las versiones más recientes y nunca elimina la activa."""

    versions = [path for path in versions_dir.iterdir() if path.is_dir()]
    if len(versions) <= 3:
        return

    versions.sort(key=lambda path: (path.stat().st_ctime, path.name))
    keep: set[str] = set()
    for version in reversed(versions):
        if len(keep) < 3 or version.name == active_id:
            keep.add(version.name)

    for version in versions:
        if version.name not in keep:
            shutil.rmtree(version)


def publish_version(build_dir: Path, versions_dir: Path, version_id: str) -> CorpusVersion:
    """Publica un build verificado como versión inmutable y poda las antiguas."""

    build_dir = Path(build_dir)
    versions_dir = Path(versions_dir)

    db_path = build_dir / "corpus.db"
    index_path = build_dir / "faiss.index"
    manifest_path = build_dir / "manifest.json"

    for path in (db_path, index_path, manifest_path):
        if not path.is_file():
            raise CorpusIntegrityError(f"falta archivo en el build: {path.name}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version_id") != version_id:
        raise CorpusIntegrityError("version_id del manifiesto no coincide")

    _verify_files(db_path, index_path, manifest)

    version_dir = versions_dir / version_id
    version_dir.parent.mkdir(parents=True, exist_ok=True)
    os.replace(build_dir, version_dir)

    version = CorpusVersion(
        version_id=version_id,
        git_commit=manifest.get("git_commit", version_id),
        db_path=version_dir / "corpus.db",
        index_path=version_dir / "faiss.index",
        db_sha256=manifest["db_sha256"],
        index_sha256=manifest["index_sha256"],
        doc_count=int(manifest.get("doc_count", 0)),
        chunk_count=int(manifest.get("chunk_count", 0)),
        activated_at=None,
    )

    _prune_versions(versions_dir, _active_version_id(versions_dir.parent))
    return version


def write_current_atomic(corpus_dir: Path, version: CorpusVersion) -> None:
    """Escribe atómicamente el puntero a la versión activa."""

    corpus_dir = Path(corpus_dir)
    current_path = corpus_dir / "current.json"
    tmp_path = corpus_dir / "current.json.tmp"

    activated_at = version.activated_at or _utc_now()
    payload = {
        "version_id": version.version_id,
        "git_commit": version.git_commit,
        "db_path": os.path.relpath(version.db_path, corpus_dir).replace(os.sep, "/"),
        "index_path": os.path.relpath(version.index_path, corpus_dir).replace(os.sep, "/"),
        "db_sha256": version.db_sha256,
        "index_sha256": version.index_sha256,
        "doc_count": version.doc_count,
        "chunk_count": version.chunk_count,
        "activated_at": activated_at,
    }

    tmp_path.write_text(json.dumps(payload), encoding="utf-8")
    _fsync_file(tmp_path)
    os.replace(tmp_path, current_path)
    _fsync_dir(corpus_dir)


def read_current(corpus_dir: Path) -> CorpusVersion | None:
    """Lee la versión activa y resuelve sus rutas a absolutas."""

    corpus_dir = Path(corpus_dir)
    current_path = corpus_dir / "current.json"
    if not current_path.is_file():
        return None

    data = json.loads(current_path.read_text(encoding="utf-8"))
    return CorpusVersion(
        version_id=data["version_id"],
        git_commit=data.get("git_commit", data["version_id"]),
        db_path=(corpus_dir / data["db_path"]).resolve(),
        index_path=(corpus_dir / data["index_path"]).resolve(),
        db_sha256=data["db_sha256"],
        index_sha256=data["index_sha256"],
        doc_count=int(data.get("doc_count", 0)),
        chunk_count=int(data.get("chunk_count", 0)),
        activated_at=data.get("activated_at"),
    )


class VersionManager:
    """Gestiona la versión activa del corpus con cache TTL y verificación."""

    def __init__(
        self,
        corpus_dir: Path,
        *,
        refresh_seconds: float = 30,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._corpus_dir = Path(corpus_dir)
        self._versions_dir = self._corpus_dir / "versions"
        self._refresh_seconds = refresh_seconds
        self._clock = clock
        self._cache: CorpusVersion | None = None
        self._last_refresh: float | None = None

    def _verify(self, version: CorpusVersion) -> None:
        _verify_files(
            version.db_path,
            version.index_path,
            {
                "db_sha256": version.db_sha256,
                "index_sha256": version.index_sha256,
            },
        )

    def refresh(self, force: bool = False) -> CorpusVersion:
        """Devuelve la versión activa, revalidando desde disco si es necesario."""

        now = self._clock()
        if (
            not force
            and self._cache is not None
            and self._last_refresh is not None
            and (now - self._last_refresh) < self._refresh_seconds
        ):
            return self._cache

        version = read_current(self._corpus_dir)
        if version is None:
            raise CorpusIntegrityError("no hay versión activa")

        self._verify(version)
        self._cache = version
        self._last_refresh = now
        return version

    def current(self) -> CorpusVersion | None:
        """Devuelve la versión cacheada sin verificar."""

        return self._cache

    def activate(self, version_id: str) -> CorpusVersion:
        """Activa una versión existente tras verificar su integridad."""

        version_dir = self._versions_dir / version_id
        if not version_dir.is_dir():
            raise CorpusIntegrityError(f"no existe la versión: {version_id}")

        manifest_path = version_dir / "manifest.json"
        if not manifest_path.is_file():
            raise CorpusIntegrityError(f"falta manifiesto de la versión: {version_id}")

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        db_path = version_dir / "corpus.db"
        index_path = version_dir / "faiss.index"
        _verify_files(db_path, index_path, manifest)

        version = CorpusVersion(
            version_id=manifest.get("version_id", version_id),
            git_commit=manifest.get("git_commit", version_id),
            db_path=db_path.resolve(),
            index_path=index_path.resolve(),
            db_sha256=manifest["db_sha256"],
            index_sha256=manifest["index_sha256"],
            doc_count=int(manifest.get("doc_count", 0)),
            chunk_count=int(manifest.get("chunk_count", 0)),
            activated_at=_utc_now(),
        )

        write_current_atomic(self._corpus_dir, version)
        self._cache = version
        self._last_refresh = self._clock()
        return version
