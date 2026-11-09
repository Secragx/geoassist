"""Interpretación de consultas: QueryWriter local y reescritura con LLM.

La capa local (:class:`LocalQueryInterpreter`) normaliza jerga de los asesores
hacia nombres canónicos de producto y corrige typos contra el vocabulario del
corpus, sin costo de latencia. La capa LLM (:class:`QueryRewriter`) reformula
la consulta con el cliente configurado, con cache y fallback a la consulta
original: nunca bloquea ni rompe la cadena de respuesta.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Mapping, Sequence

from geoassist.ingest.rules import normalize_text

MIN_TOKEN_LEN = 4
ALIAS_CUTOFF = 0.85
VOCAB_CUTOFF = 0.9

_WORD_RE = re.compile(r"\w+", re.UNICODE)


class LocalQueryInterpreter:
    """Mapea jerga y typos de la consulta a términos canónicos.

    ``aliases`` mapea vocabulario coloquial (p. ej. "geoconsola") al nombre
    canónico ("geopos"); ``vocabulary`` son los términos del corpus usados
    para corregir errores de dedo. Las palabras sin candidato cercano se
    conservan tal cual.
    """

    def __init__(self, aliases: Mapping[str, str], vocabulary: Sequence[str] = ()) -> None:
        self._aliases: dict[str, str] = {}
        for alias, canonical in aliases.items():
            normalized = normalize_text(str(alias))
            if normalized:
                self._aliases[normalized] = str(canonical)
        self._vocabulary = [normalize_text(str(term)) for term in vocabulary]

    def _canonical_for(self, normalized: str) -> str | None:
        if normalized in self._aliases:
            return self._aliases[normalized]
        alias_hits = difflib.get_close_matches(
            normalized, self._aliases.keys(), n=1, cutoff=ALIAS_CUTOFF
        )
        if alias_hits:
            return self._aliases[alias_hits[0]]
        if self._vocabulary:
            vocab_hits = difflib.get_close_matches(
                normalized, self._vocabulary, n=1, cutoff=VOCAB_CUTOFF
            )
            if vocab_hits:
                return vocab_hits[0]
        return None

    def interpret(self, text: str) -> str:
        """Devuelve el texto con los términos canónicos reemplazados in situ."""

        def replace(match: re.Match[str]) -> str:
            token = match.group(0)
            normalized = normalize_text(token)
            if len(normalized) < MIN_TOKEN_LEN:
                return token
            return self._canonical_for(normalized) or token

        return _WORD_RE.sub(replace, text)
