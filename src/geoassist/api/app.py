"""Construcción de la aplicación FastAPI."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from geoassist import __version__
from geoassist.api.readiness import ReadinessProbe
from geoassist.api.routes.health import router as health_router
from geoassist.config import Settings, get_settings


def _default_probe(settings: Settings) -> ReadinessProbe:
    """Sonda mínima: datos accesibles, modelos presentes y LLM configurado.

    No llama al LLM ni carga los modelos; solo verifica que el runtime tenga lo
    necesario para atender tráfico. Las tareas de ingesta añadirán más
    comprobaciones cuando exista una versión de corpus publicada.
    """

    def data_ready() -> bool:
        return settings.data_dir.is_dir()

    def models_ready() -> bool:
        return (
            Path(settings.embedding_model_path).is_dir()
            and Path(settings.rerank_model_path).is_dir()
        )

    def llm_ready() -> bool:
        return bool(settings.llm_base_url.strip())

    return ReadinessProbe(
        {
            "data": data_ready,
            "llm": llm_ready,
            "models": models_ready,
        }
    )


def create_app(
    settings: Settings | None = None,
    probe: ReadinessProbe | None = None,
) -> FastAPI:
    """Crea la aplicación y guarda la configuración y la sonda en ``app.state``."""

    resolved_settings = settings if settings is not None else get_settings()
    resolved_probe = probe if probe is not None else _default_probe(resolved_settings)

    expose_docs = resolved_settings.app_env != "production"
    app = FastAPI(
        title="GeoAssist",
        version=__version__,
        docs_url="/docs" if expose_docs else None,
        redoc_url="/redoc" if expose_docs else None,
        openapi_url="/openapi.json" if expose_docs else None,
    )
    app.state.settings = resolved_settings
    app.state.probe = resolved_probe
    app.include_router(health_router)
    return app
