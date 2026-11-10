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
from collections import OrderedDict
from collections.abc import Mapping, Sequence

from geoassist.ingest.rules import normalize_text
from geoassist.models import ChatMessage

MIN_TOKEN_LEN = 4
ALIAS_CUTOFF = 0.9
VOCAB_CUTOFF = 0.9

_WORD_RE = re.compile(r"\w+", re.UNICODE)

REWRITE_PROMPT = (
    "Reformula la consulta del usuario para buscar en manuales técnicos de "
    "soporte (GEOPOS, GEOINVENTARIOS, GEOPRICER, GEOPROMOTION). Corrige "
    "errores de ortografía y traduce nombres coloquiales de productos al "
    "nombre real, y la jerga del negocio a los términos de los manuales "
    "(cajero = usuario, tienda o sucursal = local, caja = terminal o local, "
    "devolución de CEDIS = salida a devolución de CEDIS). Devuelve SOLO la "
    "consulta reformulada en español, conservando números de local, cajero o "
    "equipo y términos clave. No expliques nada."
)

_MAX_REWRITE_CHARS = 300
_REWRITE_MAX_TOKENS = 96
_HISTORY_TURNS = 2


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


class QueryRewriter:
    """Reformula la consulta con un LLM, con cache y fallback a la original.

    Nunca lanza: ante cualquier error (timeout, respuesta vacía o
    sobredimensionada) devuelve la consulta tal cual entró.
    """

    def __init__(
        self,
        llm: object,
        *,
        enabled: bool = True,
        cache_size: int = 128,
        timeout_s: float = 1.2,
    ) -> None:
        self._llm = llm
        self._enabled = enabled
        self._cache_size = max(1, cache_size)
        self._timeout_s = timeout_s
        self._cache: OrderedDict[str, str] = OrderedDict()

    def rewrite(self, query: str, history: Sequence[ChatMessage]) -> str:
        """Devuelve la consulta reformulada o la original si algo falla."""

        if not self._enabled:
            return query
        cached = self._cache.get(query)
        if cached is not None:
            self._cache.move_to_end(query)
            return cached

        messages: list[dict[str, str]] = [{"role": "system", "content": REWRITE_PROMPT}]
        for message in list(history)[-_HISTORY_TURNS:]:
            messages.append({"role": message.role, "content": message.content})
        messages.append({"role": "user", "content": query})

        try:
            raw = self._llm.complete(  # type: ignore[attr-defined]
                messages,
                timeout_s=self._timeout_s,
                max_tokens=_REWRITE_MAX_TOKENS,
                max_attempts=1,
            )
        except Exception:
            return query

        cleaned = (raw or "").strip().strip('"')
        rewritten = cleaned.splitlines()[0].strip() if cleaned else ""
        if not rewritten or len(rewritten) > _MAX_REWRITE_CHARS:
            return query

        self._cache[query] = rewritten
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return rewritten
