"""Tipos base para clientes de lenguaje."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class LLMError(Exception):
    """Error traducido del cliente LLM con un tipo conocido."""

    kind: str
    message: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.message}"


@runtime_checkable
class LLMClient(Protocol):
    """Protocolo para clientes que completan mensajes de chat."""

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        timeout_s: float,
        max_tokens: int = 500,
        temperature: float = 0.1,
    ) -> str:
        """Envía los mensajes y devuelve el contenido generado."""
        ...
