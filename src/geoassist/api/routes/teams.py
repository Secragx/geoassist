"""Ruta del webhook saliente de Microsoft Teams."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from geoassist.api.app import AppDependencies
from geoassist.api.ratelimit import RateLimiter
from geoassist.logging import get_logger
from geoassist.teams.base import InvalidPayload, TeamsAuthError

logger = get_logger(__name__)

MAX_BODY_SIZE_BYTES = 64 * 1024

router = APIRouter(tags=["teams"])


def _get_dependencies(request: Request) -> AppDependencies:
    return request.app.state.dependencies


def _get_rate_limiter(request: Request) -> RateLimiter:
    limiter = request.app.state.rate_limiter
    if limiter is None:
        deps: AppDependencies = request.app.state.dependencies
        limiter = RateLimiter(limit=deps.settings.rate_limit_per_minute)
        request.app.state.rate_limiter = limiter
    return limiter


@router.post("/teams/webhook", response_model=None)
async def teams_webhook(
    request: Request,
    deps: AppDependencies = Depends(_get_dependencies),  # noqa: B008
) -> dict | Response:
    """Recibe una actividad de Teams, valida HMAC y responde."""

    body = await request.body()
    if len(body) > MAX_BODY_SIZE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Payload demasiado grande")

    headers = request.headers
    try:
        deps.adapter.verify(body, headers)
    except TeamsAuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Firma inválida") from exc

    try:
        message = deps.adapter.parse(body)
    except InvalidPayload as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Payload inválido") from exc

    if message is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    now = datetime.now(UTC)
    if deps.state is not None:
        cached = deps.state.get_cached_response(message.activity_id, now)
        if cached is not None:
            return cached

    limiter = _get_rate_limiter(request)
    if not limiter.allow(message.user_id or message.conversation_id):
        reply = deps.adapter.format_reply(
            "Has superado el límite de mensajes por minuto. Espera un momento."
        )
        return JSONResponse(content=reply, status_code=status.HTTP_429_TOO_MANY_REQUESTS)

    if deps.chat is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Servicio de chat no disponible")

    outcome = deps.chat.handle(message)
    reply = deps.adapter.format_reply(outcome.text)

    if deps.state is not None:
        deps.state.cache_response(message.activity_id, reply, now)

    return reply
