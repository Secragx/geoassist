"""Detección conservadora de aplicación a partir de una pregunta."""

from __future__ import annotations

import re
from collections.abc import Mapping

from geoassist.ingest.rules import CorpusRules, normalize_text


class AppDetector:
    """Detecta la aplicación canónica mencionada en una pregunta.

    Solo coincide con alias configurados y respeta límites de palabra, de modo
    que términos genéricos o subcadenas accidentales no se confundan con apps.
    """

    def __init__(self, aliases: Mapping[str, str]) -> None:
        self._patterns: list[tuple[re.Pattern[str], str]] = []
        for alias, canonical in aliases.items():
            normalized_alias = normalize_text(alias)
            if not normalized_alias:
                continue
            pattern = re.compile(rf"\b{re.escape(normalized_alias)}\b")
            self._patterns.append((pattern, canonical))

    def detect(self, question: str) -> str | None:
        """Devuelve la primera app canónica encontrada en la pregunta, o ``None``."""

        normalized = normalize_text(question)
        for pattern, canonical in self._patterns:
            if pattern.search(normalized):
                return canonical
        return None


def build_app_detector(rules: CorpusRules) -> AppDetector:
    """Construye un detector a partir de los alias definidos en las reglas del corpus."""

    aliases = dict(rules.aliases())

    # Las apps canónicas que no tienen alias propios también deben poder detectarse
    # (por ejemplo "geopos" en las reglas de ejemplo).
    canonical_apps = {rule.value for rule in rules.apps}
    for app in canonical_apps:
        if app and app not in aliases:
            aliases[app] = app

    return AppDetector(aliases)
