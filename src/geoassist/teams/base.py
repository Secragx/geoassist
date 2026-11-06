"""Contratos y excepciones base para adaptadores de Microsoft Teams."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from geoassist.models import InboundMessage


class TeamsAuthError(Exception):
    """La firma HMAC del webhook saliente no pudo ser validada."""


class InvalidPayload(Exception):
    """El cuerpo del webhook no cumple el contrato esperado."""


class TeamsAdapter(Protocol):
    """Contrato para adaptadores de mensajes entrantes de Teams."""

    def verify(self, body: bytes, headers: Mapping[str, str]) -> None:
        """Valida la firma HMAC del webhook saliente.

        Raises:
            TeamsAuthError: Cuando la firma falta, es inválida o no coincide.
        """
        ...

    def parse(self, body: bytes) -> InboundMessage | None:
        """Convierte el cuerpo JSON del webhook en un mensaje interno.

        Returns:
            El mensaje interno o ``None`` si la actividad debe ignorarse.

        Raises:
            InvalidPayload: Cuando falta información obligatoria.
        """
        ...

    def format_reply(self, text: str) -> dict[str, Any]:
        """Construye la actividad de respuesta esperada por Teams."""
        ...
