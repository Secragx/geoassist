"""Construcción de consultas FTS5 a partir de preguntas en lenguaje natural."""

from __future__ import annotations

import re

_FTS_OPERATORS = frozenset({"OR", "AND", "NOT", "NEAR"})
_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")


def build_fts_query(question: str) -> str:
    """Tokeniza una pregunta y devuelve una consulta FTS5 segura.

    Conserva solo tokens alfanuméricos de 3 o más caracteres, descarta los
    operadores reservados de FTS5 y entrecomilla cada término para evitar
    inyección de sintaxis. Los términos se unen con ``OR``.
    """

    tokens = _TOKEN_RE.findall(question)
    terms = [token for token in tokens if len(token) >= 3 and token.upper() not in _FTS_OPERATORS]
    return " OR ".join(f'"{term}"' for term in terms)
