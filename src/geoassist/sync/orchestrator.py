"""Orquestador de sincronización del corpus desde Git."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from geoassist.storage.versions import publish_version, write_current_atomic
from geoassist.sync.git import update_repo
from geoassist.sync.lock import FileLock, LockBusy


@dataclass
class SyncReport:
    """Resultado de una ejecución del orquestador de sincronización."""

    version_id: str
    git_commit: str
    doc_count: int
    chunk_count: int
    warnings: list[str] = field(default_factory=list)
    pdf_results: list[dict[str, Any]] = field(default_factory=list)
    exit_code: int = 0
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0


class SyncOrchestrator:
    """Coordina Git, ingesta, publicación atómica y renderizado de PDFs."""

    def __init__(
        self,
        repo_dir: Path,
        repo_url: str,
        corpus_dir: Path,
        ingestor: Any,
        renderer: Any = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._repo_dir = Path(repo_dir)
        self._repo_url = repo_url
        self._corpus_dir = Path(corpus_dir)
        self._ingestor = ingestor
        self._renderer = renderer
        self._clock = clock

    def run(self, no_pdf: bool = False) -> SyncReport:
        """Ejecuta una sincronización completa y devuelve el reporte."""

        corpus_dir = self._corpus_dir
        corpus_dir.mkdir(parents=True, exist_ok=True)
        lock_path = corpus_dir / "sync.lock"

        started_at = _utc_now()
        started = self._clock()

        try:
            with FileLock(lock_path):
                return self._run_locked(corpus_dir, no_pdf, started_at, started)
        except LockBusy:
            finished_at = _utc_now()
            duration = self._clock() - started
            report = SyncReport(
                version_id="",
                git_commit="",
                doc_count=0,
                chunk_count=0,
                warnings=["lock ocupado por otro proceso"],
                exit_code=3,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=duration,
            )
            _write_report(corpus_dir, report)
            return report

    def _run_locked(
        self,
        corpus_dir: Path,
        no_pdf: bool,
        started_at: str,
        started: float,
    ) -> SyncReport:
        version_id = ""
        git_commit = ""
        doc_count = 0
        chunk_count = 0
        warnings: list[str] = []
        pdf_results: list[dict[str, Any]] = []
        exit_code = 1

        try:
            git_commit = update_repo(self._repo_dir, self._repo_url, branch="main")
            version_id = f"{_utc_timestamp()}-{git_commit[:7]}"
            build_dir = corpus_dir / f"build-{version_id}"

            ingest_report = self._ingestor.build(
                version_id,
                build_dir,
                git_commit=git_commit,
            )
            doc_count = ingest_report.doc_count
            chunk_count = ingest_report.chunk_count
            warnings.extend(ingest_report.warnings)

            versions_dir = corpus_dir / "versions"
            version = publish_version(build_dir, versions_dir, version_id)
            write_current_atomic(corpus_dir, version)

            pdf_results = self._render_pdfs(no_pdf)
            exit_code = 2 if any(not r.get("ok", True) for r in pdf_results) else 0
        except Exception as exc:  # noqa: BLE001 - captura errores previos a publicar
            warnings.append(str(exc))
            exit_code = 1

        finished_at = _utc_now()
        duration = self._clock() - started
        report = SyncReport(
            version_id=version_id,
            git_commit=git_commit,
            doc_count=doc_count,
            chunk_count=chunk_count,
            warnings=warnings,
            pdf_results=pdf_results,
            exit_code=exit_code,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration,
        )
        _write_report(corpus_dir, report)
        return report

    def _render_pdfs(self, no_pdf: bool) -> list[dict[str, Any]]:
        if no_pdf or self._renderer is None:
            return []
        try:
            if hasattr(self._renderer, "render_all"):
                try:
                    results = self._renderer.render_all()
                except TypeError:
                    results = self._renderer.render_all(self._repo_dir, self._corpus_dir)
            elif hasattr(self._renderer, "render"):
                try:
                    results = self._renderer.render()
                except TypeError:
                    results = self._renderer.render(self._repo_dir, self._corpus_dir)
            else:
                results = []
            out: list[dict[str, Any]] = []
            for r in results:
                if isinstance(r, dict):
                    out.append(r)
                else:
                    status = getattr(r, "status", None)
                    ok = status in {"generated", "skipped"}
                    out.append(
                        {
                            "ok": ok,
                            "rel_path": getattr(r, "rel_path", getattr(r, "path", None)),
                            "status": status,
                            "reason": getattr(r, "reason", getattr(r, "error", None)),
                        }
                    )
            if not out:
                return []
            return out
        except Exception as exc:  # noqa: BLE001 - el PDF no debe abortar la publicación
            return [{"ok": False, "error": str(exc)}]


def _utc_now() -> str:
    """Devuelve la fecha actual como ISO-8601 UTC con sufijo ``Z``."""

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _utc_timestamp() -> str:
    """Devuelve un timestamp UTC compacto para formar ``version_id``."""

    return datetime.now(UTC).strftime("%Y%m%d%H%M%S")


def _write_report(corpus_dir: Path, report: SyncReport) -> None:
    """Escribe ``sync_report.json`` de forma atómica."""

    corpus_dir = Path(corpus_dir)
    corpus_dir.mkdir(parents=True, exist_ok=True)
    report_path = corpus_dir / "sync_report.json"
    tmp_path = corpus_dir / "sync_report.json.tmp"

    payload = {
        "version_id": report.version_id,
        "git_commit": report.git_commit,
        "doc_count": report.doc_count,
        "chunk_count": report.chunk_count,
        "warnings": report.warnings,
        "pdf_results": report.pdf_results,
        "exit_code": report.exit_code,
        "started_at": report.started_at,
        "finished_at": report.finished_at,
        "duration_seconds": report.duration_seconds,
    }

    tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, report_path)
