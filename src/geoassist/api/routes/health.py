"""Rutas de salud y disponibilidad."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from geoassist.api.readiness import ReadinessProbe

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness")
async def liveness() -> dict[str, str]:
    """Responde siempre que el proceso esté vivo, sin tocar dependencias."""

    return {"status": "ok"}


@router.get("/ready", summary="Readiness")
async def readiness(request: Request, response: Response) -> dict[str, object]:
    """Ejecuta la sonda y devuelve 503 si alguna comprobación falla."""

    probe: ReadinessProbe = request.app.state.probe
    ready, checks = probe.run()
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"ready": ready, "checks": checks}
