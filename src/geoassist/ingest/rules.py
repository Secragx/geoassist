"""Reglas configurables de ruta → aplicación y estado del corpus.

Las reglas se cargan de ``config/corpus_rules.yml`` y se evalúan en orden: la
primera coincidencia gana. La comparación usa una forma normalizada de la ruta
(NFKD, sin acentos, minúsculas y espacios colapsados) para que los nombres de
carpeta del repositorio de manuales no sean parte del contrato de la ingesta.
"""

from __future__ import annotations

import logging
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

STATUSES = ("approved", "pending", "archived", "unknown")
CANONICAL_APPS = ("geopos", "geoinventarios", "geopricer", "geopromotion", "unknown")

DEFAULT_APP = "unknown"
DEFAULT_STATUS = "unknown"

_LOGGER = logging.getLogger(__name__)


def normalize_text(value: str) -> str:
    """Normaliza un valor o ruta para comparaciones sin acentos ni mayúsculas."""

    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.lower().split())


@dataclass(frozen=True, slots=True)
class _MatchRule:
    """Regla de subcadena ya normalizada."""

    match: str
    value: str


@dataclass(frozen=True, slots=True)
class CorpusRules:
    """Conjunto inmutable de reglas de exclusión, aplicación y estado."""

    ignore: tuple[str, ...]
    apps: tuple[_MatchRule, ...]
    statuses: tuple[_MatchRule, ...]
    default_app: str
    default_status: str
    _aliases: Mapping[str, str]

    @classmethod
    def load(cls, path: Path) -> CorpusRules:
        """Carga y valida las reglas desde un archivo YAML."""

        path = Path(path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if raw is None:
            raw = {}
        if not isinstance(raw, Mapping):
            raise ValueError(f"las reglas de corpus deben ser un mapa: {path}")

        ignore = tuple(
            pattern
            for pattern in (normalize_text(str(item)) for item in _text_list(raw.get("ignore")))
            if pattern
        )

        app_entries = _mapping_list(raw.get("apps"), "apps")
        aliases: dict[str, str] = {}
        for entry in app_entries:
            app = normalize_text(str(entry.get("app", "")))
            for alias in _text_list(entry.get("aliases")):
                aliases[normalize_text(str(alias))] = app

        apps: list[_MatchRule] = []
        for entry in app_entries:
            match = normalize_text(str(entry.get("match", "")))
            app = normalize_text(str(entry.get("app", "")))
            canonical = _canonical_app(app, aliases)
            if not match or canonical == DEFAULT_APP:
                _LOGGER.warning(
                    "regla de aplicación inválida ignorada (match=%r, app=%r)", match, app
                )
                continue
            apps.append(_MatchRule(match=match, value=canonical))

        statuses: list[_MatchRule] = []
        for entry in _mapping_list(raw.get("status"), "status"):
            match = normalize_text(str(entry.get("match", "")))
            status = normalize_text(str(entry.get("status", "")))
            if not match or status not in STATUSES or status == DEFAULT_STATUS:
                _LOGGER.warning(
                    "regla de estado inválida ignorada (match=%r, status=%r)", match, status
                )
                continue
            statuses.append(_MatchRule(match=match, value=status))

        defaults = raw.get("defaults")
        if defaults is None:
            defaults = {}
        if not isinstance(defaults, Mapping):
            raise ValueError("defaults debe ser un mapa en las reglas de corpus")

        default_app = _canonical_app(normalize_text(str(defaults.get("app", ""))), aliases)
        raw_default_status = normalize_text(str(defaults.get("status", "")))
        default_status = raw_default_status if raw_default_status in STATUSES else DEFAULT_STATUS

        return cls(
            ignore=ignore,
            apps=tuple(apps),
            statuses=tuple(statuses),
            default_app=default_app,
            default_status=default_status,
            _aliases=aliases,
        )

    def is_ignored(self, rel_path: str) -> bool:
        """Indica si la ruta relativa coincide con algún patrón de exclusión."""

        normalized = normalize_text(rel_path)
        return any(_matches_ignore(normalized, pattern) for pattern in self.ignore)

    def app_for(self, rel_path: str) -> str:
        """Devuelve la aplicación de la primera regla que coincide con la ruta."""

        normalized = normalize_text(rel_path)
        for rule in self.apps:
            if rule.match in normalized:
                return rule.value
        return self.default_app

    def status_for(self, rel_path: str) -> str:
        """Devuelve el estado de la primera regla que coincide con la ruta."""

        normalized = normalize_text(rel_path)
        for rule in self.statuses:
            if rule.match in normalized:
                return rule.value
        return self.default_status

    def aliases(self) -> dict[str, str]:
        """Devuelve el mapa normalizado de alias a aplicación canónica."""

        return dict(self._aliases)


def _canonical_app(value: str, aliases: Mapping[str, str]) -> str:
    canonical = aliases.get(value, value)
    if canonical in CANONICAL_APPS:
        return canonical
    return DEFAULT_APP


def _matches_ignore(normalized_path: str, pattern: str) -> bool:
    """Evalúa un patrón ``fnmatch`` tratando un ``**/`` inicial como cero o más carpetas."""

    if fnmatchcase(normalized_path, pattern):
        return True
    if pattern.startswith("**/"):
        return fnmatchcase(normalized_path, pattern[3:])
    return False


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError("se esperaba una lista de textos en las reglas de corpus")
    return [str(item) for item in value]


def _mapping_list(value: Any, key: str) -> list[Mapping[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{key} debe ser una lista en las reglas de corpus")
    entries: list[Mapping[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError(f"cada elemento de {key} debe ser un mapa")
        entries.append(item)
    return entries
