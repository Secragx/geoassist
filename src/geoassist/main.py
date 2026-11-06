"""Punto de entrada ASGI de GeoAssist."""

from __future__ import annotations

from fastapi import FastAPI

from geoassist.api.app import create_app
from geoassist.config import get_settings
from geoassist.logging import configure_logging

_settings = get_settings()
configure_logging(_settings.log_level)

app: FastAPI = create_app(_settings)
