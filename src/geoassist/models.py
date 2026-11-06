"""Modelos de datos compartidos entre servicios de GeoAssist."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """Mensaje de chat con rol, contenido y marca temporal ISO-8601 UTC."""

    role: str
    content: str
    created_at: str


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """Mensaje entrante de un canal como Microsoft Teams."""

    activity_id: str
    text: str
    user_id: str
    conversation_id: str
    tenant_id: str
    service_url: str


@dataclass(frozen=True, slots=True)
class SessionState:
    """Estado de una sesión de conversación: clave, hint y historial."""

    session_key: str
    app_hint: str | None
    history: tuple[ChatMessage, ...]
