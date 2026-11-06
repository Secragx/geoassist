"""Resolución de metadatos de un manual.

La precedencia es frontmatter → reglas de ruta → valores por defecto. Un valor
desconocido no aborta la ingesta: se indexa como ``unknown`` y se reporta en el
log para que el operador corrija el frontmatter o las reglas.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from geoassist.ingest.rules import (
    CANONICAL_APPS,
    STATUSES,
    CorpusRules,
    normalize_text,
)

_LOGGER = logging.getLogger(__name__)

PLACEHOLDER_TITLES = frozenset({"nombre del manual", "todo"})
GENERIC_HEADINGS = frozenset({"objeto", "alcance", "definiciones", "propiedades", "componentes"})
APPROVAL_HEADING_MARKERS = ("aprobacion",)


@dataclass(frozen=True, slots=True)
class DocMetadata:
    """Metadatos resueltos que acompañan a un documento del corpus."""

    rel_path: str
    title: str
    app: str
    status: str
    version: str | None
    frontmatter: Mapping[str, Any]


class MetadataResolver:
    """Combina frontmatter, reglas de ruta y defaults para un documento."""

    def __init__(self, rules: CorpusRules) -> None:
        self._rules = rules
        self._aliases = rules.aliases()

    def resolve(
        self,
        rel_path: str,
        frontmatter: Mapping[str, Any],
        first_h1: str | None,
    ) -> DocMetadata:
        """Resuelve los metadatos de ``rel_path`` con la precedencia documentada."""

        metadata = dict(frontmatter)
        app = self._resolve_app(metadata, rel_path)
        status = self._resolve_status(metadata, rel_path)
        if app == "unknown":
            _LOGGER.warning(
                "aplicación desconocida; se indexa como unknown (rel_path=%s, app=%r)",
                rel_path,
                metadata.get("app"),
            )
        if status == "unknown":
            _LOGGER.warning(
                "estado desconocido; se indexa como unknown (rel_path=%s, status=%r)",
                rel_path,
                metadata.get("status"),
            )
        return DocMetadata(
            rel_path=rel_path,
            title=_resolve_title(metadata.get("title"), first_h1, rel_path),
            app=app,
            status=status,
            version=_resolve_version(metadata.get("version")),
            frontmatter=metadata,
        )

    def _resolve_app(self, frontmatter: Mapping[str, Any], rel_path: str) -> str:
        raw = frontmatter.get("app")
        if isinstance(raw, str) and normalize_text(raw):
            value = normalize_text(raw)
            canonical = self._aliases.get(value, value)
            return canonical if canonical in CANONICAL_APPS else "unknown"
        return self._rules.app_for(rel_path)

    def _resolve_status(self, frontmatter: Mapping[str, Any], rel_path: str) -> str:
        raw = frontmatter.get("status")
        if isinstance(raw, str) and normalize_text(raw):
            value = normalize_text(raw)
            return value if value in STATUSES else "unknown"
        return self._rules.status_for(rel_path)


def _resolve_version(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _resolve_title(raw_title: Any, first_h1: str | None, rel_path: str) -> str:
    if isinstance(raw_title, str):
        title = raw_title.strip()
        if title and not _is_placeholder(title):
            return title

    heading = first_h1.strip() if isinstance(first_h1, str) else ""
    if heading and not _is_generic_heading(heading):
        return heading

    filename = Path(rel_path).stem.replace("_", " ")
    derived = " ".join(filename.split())
    return derived or "unknown"


def _is_placeholder(title: str) -> bool:
    return normalize_text(title).rstrip(" .:") in PLACEHOLDER_TITLES


def _is_generic_heading(heading: str) -> bool:
    normalized = normalize_text(heading)
    if not normalized:
        return True
    if normalized.rstrip(" .:") in GENERIC_HEADINGS:
        return True
    return any(marker in normalized for marker in APPROVAL_HEADING_MARKERS)
