"""Adaptador para webhooks salientes de Microsoft Teams."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import html
import json
import re
from collections.abc import Mapping
from typing import Any

from geoassist.logging import get_logger
from geoassist.models import InboundMessage
from geoassist.teams.base import InvalidPayload, TeamsAuthError

logger = get_logger(__name__)

_MENTION_RE = re.compile(r"<at>.*?</at>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean_teams_text(text: str) -> str:
    """Normaliza el texto HTML de Teams a texto plano.

    Quita menciones ``<at>…</at>`` (con cualquier nombre visible), elimina
    etiquetas HTML y decodifica entidades (``&nbsp;`` etc.) colapsando los
    espacios. Teams puede enviar el mensaje con formato HTML según el cliente.
    """
    without_mentions = _MENTION_RE.sub(" ", text)
    without_tags = _TAG_RE.sub(" ", without_mentions)
    unescaped = html.unescape(without_tags).replace("\xa0", " ")
    return " ".join(unescaped.split())


def _require_dict(value: Any, name: str) -> dict[Any, Any]:
    if not isinstance(value, dict):
        raise InvalidPayload(f"{name} must be an object")
    return value


class OutgoingWebhookAdapter:
    """Valida, parsea y formatea mensajes de un webhook saliente de Teams."""

    def __init__(self, secret: str, bot_name: str, *, allow_unsigned: bool = False) -> None:
        self._secret = secret.encode()
        # bot_name se conserva por compatibilidad; la limpieza de menciones
        # acepta cualquier nombre (Teams puede mostrar el bot como "Chatbot").
        self._bot_name = bot_name
        # Solo para pruebas locales: con secreto vacío y allow_unsigned, verify()
        # no exige firma. En producción (allow_unsigned=False) jamás se salta.
        self._allow_unsigned = allow_unsigned
        if allow_unsigned and not self._secret:
            logger.warning(
                "HMAC DESHABILITADO: APP_ENV=development sin TEAMS_SECURITY_TOKEN; "
                "el webhook NO valida firmas"
            )

    def verify(self, body: bytes, headers: Mapping[str, str]) -> None:
        """Valida la firma HMAC enviada por Teams.

        Raises:
            TeamsAuthError: Si falta la cabecera o la firma no coincide.
        """
        if self._allow_unsigned and not self._secret:
            logger.warning("HMAC no validado: modo desarrollo sin TEAMS_SECURITY_TOKEN")
            return

        auth_header = headers.get("Authorization", "")
        if not auth_header.startswith("HMAC "):
            raise TeamsAuthError("Missing or malformed Authorization header")

        expected_signature = auth_header[5:].strip()
        try:
            expected_digest = base64.b64decode(expected_signature)
        except (ValueError, binascii.Error) as exc:
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

        from_payload = _require_dict(payload.get("from"), "from")
        user_id = from_payload.get("id")
        if not user_id:
            raise InvalidPayload("Missing from.id")

        conversation_payload = _require_dict(payload.get("conversation"), "conversation")
        conversation_id = conversation_payload.get("id")
        if not conversation_id:
            raise InvalidPayload("Missing conversation.id")

        text = payload.get("text")
        if text is None:
            raise InvalidPayload("Missing text")
        text = _clean_teams_text(text)

        channel_data = _require_dict(payload.get("channelData", {}), "channelData")
        tenant_payload = _require_dict(channel_data.get("tenant", {}), "channelData.tenant")
        tenant_id = tenant_payload.get("id", "")
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
