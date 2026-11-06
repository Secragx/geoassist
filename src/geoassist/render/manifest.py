"""Generación del manifiesto de PDFs."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(8192), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass
class PdfManifestEntry:
    """Entrada del manifiesto de un PDF generado."""

    manual_path: str
    pdf_path: str
    source_sha256: str
    pdf_sha256: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "manual_path": self.manual_path,
            "pdf_path": self.pdf_path,
            "source_sha256": self.source_sha256,
            "pdf_sha256": self.pdf_sha256,
        }


@dataclass
class PdfManifest:
    """Manifiesto completo de PDFs."""

    entries: list[PdfManifestEntry] = field(default_factory=list)
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [entry.to_dict() for entry in self.entries],
            "generated_at": self.generated_at or _utc_now(),
        }


class ManifestBuilder:
    """Construye el manifiesto a partir de un directorio de manuales y salida."""

    def __init__(self, manuals_dir: Path | None = None, output_dir: Path | None = None) -> None:
        self._manuals_dir = Path(manuals_dir) if manuals_dir is not None else None
        self._output_dir = Path(output_dir) if output_dir is not None else None

    def build(
        self, manuals_dir: Path | str | None = None, output_dir: Path | str | None = None
    ) -> PdfManifest:
        md = Path(manuals_dir) if manuals_dir is not None else self._manuals_dir
        od = Path(output_dir) if output_dir is not None else self._output_dir
        if md is None or od is None:
            raise ValueError("manuals_dir y output_dir son obligatorios")
        md = Path(md)
        od = Path(od)
        od.mkdir(parents=True, exist_ok=True)

        entries: list[PdfManifestEntry] = []
        if md.is_dir():
            for path in sorted(md.rglob("*.md")):
                rel = path.relative_to(md).as_posix()
                pdf = od / Path(rel).with_suffix(".pdf")
                pdf_rel = pdf.relative_to(od).as_posix() if pdf.is_relative_to(od) else str(pdf)
                source_sha = _sha256_file(path)
                pdf_sha = _sha256_file(pdf) if pdf.is_file() else ""
                entries.append(
                    PdfManifestEntry(
                        manual_path=rel,
                        pdf_path=pdf_rel,
                        source_sha256=source_sha,
                        pdf_sha256=pdf_sha,
                    )
                )

        manifest = PdfManifest(entries=entries, generated_at=_utc_now())
        manifest_path = od / "manifest.json"
        tmp = od / "manifest.json.tmp"
        tmp.write_text(
            json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, manifest_path)
        return manifest
