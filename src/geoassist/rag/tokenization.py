"""Conteo de tokens con el tokenizer XLM-R real del modelo BGE-M3.

No se estima por palabras: el chunker necesita los mismos límites que ve el
modelo de embeddings, y el tokenizer se carga desde el ``tokenizer.json``
empaquetado con la versión de modelos.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from tokenizers import Tokenizer


class TokenCounter(Protocol):
    """Contrato mínimo de conteo de tokens usado por el chunker."""

    def count(self, text: str) -> int:
        """Devuelve la cantidad de tokens que produce ``text``."""

        ...


class XlmRobertaTokenCounter:
    """Cuenta tokens con un ``tokenizers.Tokenizer`` cargado desde disco."""

    def __init__(self, tokenizer_path: Path) -> None:
        self._tokenizer = Tokenizer.from_file(str(tokenizer_path))

    def count(self, text: str) -> int:
        """Devuelve ``len(encoding.ids)`` para ``text``."""

        return len(self._tokenizer.encode(text).ids)
