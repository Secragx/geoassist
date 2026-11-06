"""Descubrimiento de manuales Markdown en el repositorio de origen.

El cargador es la frontera entre el sistema de archivos y la ingesta: entrega
documentos con rutas POSIX relativas a la raíz del repositorio, texto UTF-8
normalizado, frontmatter y advertencias. Nunca aborta el lote por un archivo
ilegible, un YAML inválido o una imagen ausente.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

import frontmatter

from geoassist.ingest.rules import CorpusRules

_LOGGER = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_H1_RE = re.compile(r"^ {0,3}#[ \t]+(.*)$")
_CLOSING_HASHES_RE = re.compile(r"[ \t]+#+[ \t]*$")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*(?P<src>(?:[^()\\]|\\.|\([^()]*\))+?)\s*\)")
_IMAGE_TITLE_SUFFIX_RE = re.compile(r"""\s+(?:"[^"]*"|'[^']*')\s*$""")
_REMOTE_SCHEMES = frozenset({"data", "http", "https"})


@dataclass(frozen=True, slots=True)
class RawDocument:
    """Documento crudo descubierto, antes de resolver metadatos y chunking."""

    rel_path: str
    text: str
    frontmatter: Mapping[str, Any]
    content_hash: str
    warnings: tuple[str, ...] = ()


class ManualLoader:
    """Descubre los ``.md`` indexables bajo la raíz de manuales."""

    def __init__(
        self,
        repo_root: Path,
        manuals_root_rel: str,
        rules: CorpusRules,
        assets_root_rel: str = "data_source/assets",
    ) -> None:
        self._repo_root = _absolute(Path(repo_root))
        self._manuals_root = _absolute(Path(repo_root) / manuals_root_rel)
        self._assets_root = _absolute(Path(repo_root) / assets_root_rel)
        self._rules = rules
        self._asset_names: set[str] | None = None
        self._asset_stems: set[str] | None = None

    @property
    def repo_root(self) -> Path:
        """Raíz del repositorio de manuales."""

        return self._repo_root

    def discover_ignored(self) -> list[tuple[str, str]]:
        """Devuelve los ``.md`` ignorados por las reglas, con su motivo."""

        manuals_root = self._manuals_root
        if not manuals_root.is_dir():
            return []

        repo_root = self._repo_root
        real_repo_root = repo_root.resolve()
        real_manuals_root = manuals_root.resolve()
        if not real_manuals_root.is_relative_to(real_repo_root):
            return []

        ignored: list[tuple[str, str]] = []
        for path in manuals_root.rglob("*.md"):
            absolute_path = _absolute(path)
            try:
                real_path = absolute_path.resolve()
            except OSError:
                continue
            if not real_path.is_relative_to(real_repo_root):
                continue
            if not real_path.is_file():
                continue

            rel_path = absolute_path.relative_to(repo_root).as_posix()
            if self._rules.is_ignored(rel_path):
                ignored.append((rel_path, "matched ignore rule"))

        ignored.sort(key=lambda item: item[0])
        return ignored

    def discover(self) -> list[RawDocument]:
        """Devuelve los documentos permitidos, ordenados por ``rel_path``."""

        manuals_root = self._manuals_root
        if not manuals_root.is_dir():
            _LOGGER.warning("raíz de manuales inexistente: %s", manuals_root)
            return []

        repo_root = self._repo_root
        real_repo_root = repo_root.resolve()
        real_manuals_root = manuals_root.resolve()
        if not real_manuals_root.is_relative_to(real_repo_root):
            _LOGGER.warning("raíz de manuales fuera del repositorio: %s", manuals_root)
            return []

        candidates: list[tuple[str, Path]] = []
        for path in manuals_root.rglob("*.md"):
            absolute_path = _absolute(path)
            try:
                real_path = absolute_path.resolve()
            except OSError as exc:
                _LOGGER.warning("no se pudo resolver %s: %s", absolute_path, exc)
                continue
            if not real_path.is_relative_to(real_repo_root):
                _LOGGER.warning("symlink fuera de la raíz del repositorio: %s", absolute_path)
                continue
            if not real_path.is_file():
                continue
            rel_path = absolute_path.relative_to(repo_root).as_posix()
            if self._rules.is_ignored(rel_path):
                continue
            candidates.append((rel_path, absolute_path))

        candidates.sort(key=lambda item: item[0])
        return [self._load(rel_path, path) for rel_path, path in candidates]

    def _load(self, rel_path: str, path: Path) -> RawDocument:
        warnings: list[str] = []

        try:
            raw = path.read_bytes()
        except OSError as exc:
            warnings.append(f"archivo ilegible: {exc}")
            text = ""
            metadata: dict[str, Any] = {}
        else:
            decoded, decode_warning = _decode_utf8(raw)
            if decode_warning is not None:
                warnings.append(decode_warning)
            normalized = _normalize_newlines(decoded)
            metadata, text, frontmatter_warnings = _parse_frontmatter(normalized)
            warnings.extend(frontmatter_warnings)
            warnings.extend(self._image_warnings(text))

        normalized_text = _normalize_newlines(text)
        for warning in warnings:
            _LOGGER.warning("%s: %s", rel_path, warning)

        return RawDocument(
            rel_path=rel_path,
            text=normalized_text,
            frontmatter=metadata,
            content_hash=hashlib.sha256(normalized_text.encode("utf-8")).hexdigest(),
            warnings=tuple(warnings),
        )

    def _image_warnings(self, text: str) -> list[str]:
        names, stems = self._asset_index()
        warnings: list[str] = []
        for match in _IMAGE_RE.finditer(text):
            raw_src = _IMAGE_TITLE_SUFFIX_RE.sub("", match.group("src").strip()).strip()
            if raw_src.startswith("<") and raw_src.endswith(">"):
                raw_src = raw_src[1:-1].strip()
            if not raw_src:
                continue
            if urlparse(raw_src).scheme.lower() in _REMOTE_SCHEMES:
                continue
            clean_src = unquote(raw_src.split("#", 1)[0].split("?", 1)[0]).strip()
            name = PurePosixPath(clean_src.replace("\\", "/")).name
            if not name:
                continue
            if name in names or PurePosixPath(name).stem in stems:
                continue
            warnings.append(f"imagen ausente: {raw_src}")
        return warnings

    def _asset_index(self) -> tuple[set[str], set[str]]:
        if self._asset_names is None:
            names: set[str] = set()
            stems: set[str] = set()
            assets_root = self._assets_root
            if assets_root.is_dir():
                real_repo_root = self._repo_root.resolve()
                for path in assets_root.rglob("*"):
                    if not path.is_file():
                        continue
                    try:
                        real_path = path.resolve()
                    except OSError:
                        continue
                    if not real_path.is_relative_to(real_repo_root):
                        continue
                    names.add(path.name)
                    stems.add(path.stem)
            self._asset_names = names
            self._asset_stems = stems
        return self._asset_names, self._asset_stems or set()


def extract_first_h1(markdown: str) -> str | None:
    """Devuelve el texto del primer encabezado ATX de nivel 1 fuera de fences."""

    fence_char: str | None = None
    fence_length = 0

    for line in markdown.splitlines():
        fence = _FENCE_RE.match(line)
        if fence_char is None:
            if fence is not None:
                fence_char = fence.group(1)[0]
                fence_length = len(fence.group(1))
                continue
        else:
            if (
                fence is not None
                and fence.group(1)[0] == fence_char
                and len(fence.group(1)) >= fence_length
            ):
                fence_char = None
            continue

        heading = _H1_RE.match(line)
        if heading is None:
            continue
        text = _CLOSING_HASHES_RE.sub("", heading.group(1)).strip()
        if text:
            return text
    return None


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _decode_utf8(raw: bytes) -> tuple[str, str | None]:
    try:
        return raw.decode("utf-8-sig"), None
    except UnicodeDecodeError as exc:
        decoded = raw.decode("utf-8-sig", errors="replace")
        return decoded, f"archivo no decodificable como UTF-8: {exc}"


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str, list[str]]:
    try:
        post = frontmatter.loads(text)
    except Exception as exc:  # noqa: BLE001 - un YAML roto no debe abortar el lote
        return (
            {},
            _strip_frontmatter_block(text).strip(),
            [f"frontmatter YAML inválido: {exc}"],
        )

    if not isinstance(post.metadata, Mapping):
        return {}, text, ["frontmatter YAML inválido: se esperaba un mapa"]
    return dict(post.metadata), post.content, []


def _strip_frontmatter_block(text: str) -> str:
    """Quita un bloque de frontmatter aunque su YAML no se pueda interpretar."""

    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return text
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() in {"---", "..."}:
            return "".join(lines[index + 1 :])
    return text
