"""Adaptador para webhooks salientes de Microsoft Teams."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from typing import Any

from geoassist.models import InboundMessage
from geoassist.teams.base import InvalidPayload, TeamsAuthError


class OutgoingWebhookAdapter:
    """Valida, parsea y formatea mensajes de un webhook saliente de Teams."""

    def __init__(self, secret: str, bot_name: str) -> None:
        self._secret = secret.encode()
        self._bot_name = bot_name
        self._mention_pattern = re.compile(
            rf"<at>[^<]*{re.escape(bot_name)}[^<]*</at>", re.IGNORECASE
        )

    def verify(self, body: bytes, headers: dict[str, str]) -> None:
        """Valida la firma HMAC enviada por Teams.

        Raises:
            TeamsAuthError: Si falta la cabecera o la firma no coincide.
        """
        auth_header = headers.get("Authorization", "")
        if not auth_header.startswith("HMAC "):
            raise TeamsAuthError("Missing or malformed Authorization header")

        expected_signature = auth_header[5:].strip()
        try:
            expected_digest = base64.b64decode(expected_signature)
        except Exception as exc:
            raise TeamsAuthError("Invalid base64 signature") from exc

        computed_digest = hmac.new(self._secret, body, hashlib.sha256).digest()
        if not hmac.compare_digest(computed_digest, expected_digest):
            raise TeamsAuthError("Signature mismatch")

    def parse(self, body: bytes) -> InboundMessage | None:
        """Convierte el cuerpo JSON de Teams en un mensaje interno.

        Returns:
            El mensaje interno o ``None`` si la actividad no es de tipo ``message``.

        Raises:
            InvalidPayload: Si falta información obligatoria o el JSON es inválido.
        """
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise InvalidPayload("Invalid JSON body") from exc

        if payload.get("type") != "message":
            return None

        activity_id = payload.get("id")
        if not activity_id:
            raise InvalidPayload("Missing activity id")

        user_id = payload.get("from", {}).get("id")
        if not user_id:
            raise InvalidPayload("Missing from.id")

        conversation_id = payload.get("conversation", {}).get("id")
        if not conversation_id:
            raise InvalidPayload("Missing conversation.id")

        text = payload.get("text", "")
        text = self._mention_pattern.sub("", text).strip()

        tenant_id = payload.get("channelData", {}).get("tenant", {}).get("id", "")
        service_url = payload.get("serviceUrl", "")

        return InboundMessage(
            activity_id=activity_id,
            text=text,
            user_id=user_id,
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            service_url=service_url,
        )

    def format_reply(self, text: str) -> dict[str, Any]:
        """Construye la actividad de respuesta esperada por Teams."""
        return {"type": "message", "text": text}
