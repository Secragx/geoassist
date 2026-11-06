"""Configuración de la aplicación validada al arranque.

Todos los valores se leen del entorno (o de un `.env` en desarrollo) mediante
``pydantic-settings``. En producción la validación es *fail-closed*: el proceso
no arranca si falta un secreto, una política de datos o una ruta de modelo.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

AppEnv = Literal["development", "production"]
LLMDataPolicy = Literal["external_approved", "on_prem_only"]


class Settings(BaseSettings):
    """Configuración completa del servicio (tabla 14.2 del diseño)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="forbid",
    )

    app_env: AppEnv = "development"

    # Teams
    teams_security_token: SecretStr = SecretStr("")
    teams_bot_name: str = "GeoAssist"

    # Cliente LLM compatible con OpenAI
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = "deepseek-chat"
    llm_timeout_ms: int = Field(default=2500, gt=0)
    llm_data_policy: LLMDataPolicy | None = None
    llm_allowed_hosts: Annotated[tuple[str, ...], NoDecode] = ()

    # Modelos ONNX
    embedding_model_path: str = "/app/models/bge-m3-int8"
    rerank_model_path: str = "/app/models/mmarco-mMiniLMv2-L12-H384-v1-int8"
    rerank_min_score: float = 0.0
    models_artifact_url: str = ""
    models_artifact_sha256: str = ""

    # Rutas internas y del repositorio de manuales
    data_dir: Path = Path("/data")
    manuals_dir: Path = Path("/manuals")
    manuals_root_rel: str = "data_source/manuales"
    assets_root_rel: str = "data_source/assets"

    # Sesiones y límites
    session_ttl_minutes: int = Field(default=120, gt=0)
    rate_limit_per_minute: int = Field(default=6, gt=0)

    # Diagnóstico
    debug_retrieval: bool = False
    debug_token: SecretStr = SecretStr("")
    log_question_text: bool = False
    log_level: str = "INFO"

    # Sincronización manual
    sync_repo_url: str = "git@github.com:Secragx/manuales_soporte_L2.git"
    sync_git_token: SecretStr = SecretStr("")

    @field_validator("llm_data_policy", mode="before")
    @classmethod
    def _empty_policy_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("llm_allowed_hosts", mode="before")
    @classmethod
    def _parse_allowed_hosts(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return ()
            if text.startswith("["):
                try:
                    parsed = json.loads(text)
                except ValueError as exc:
                    raise ValueError(
                        "LLM_ALLOWED_HOSTS debe ser una lista JSON o una lista separada por comas"
                    ) from exc
                if not isinstance(parsed, list):
                    raise ValueError("LLM_ALLOWED_HOSTS debe ser una lista")
                return tuple(str(host).strip() for host in parsed if str(host).strip())
            return tuple(host.strip() for host in text.split(",") if host.strip())
        return value

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in logging.getLevelNamesMapping():
            raise ValueError(f"nivel de log inválido: {value!r}")
        return normalized

    @model_validator(mode="after")
    def _validate_security(self) -> Settings:
        if self.debug_retrieval and not self.debug_token.get_secret_value().strip():
            raise ValueError("DEBUG_TOKEN es obligatorio cuando DEBUG_RETRIEVAL=true")

        if self.llm_data_policy == "on_prem_only":
            host = urlparse(self.llm_base_url).hostname
            allowed = {item.lower() for item in self.llm_allowed_hosts if item.strip()}
            if host is None or host.lower() not in allowed:
                raise ValueError(
                    "LLM_DATA_POLICY=on_prem_only exige que el host de LLM_BASE_URL "
                    "esté en LLM_ALLOWED_HOSTS"
                )

        if self.app_env == "production":
            if not self.teams_security_token.get_secret_value().strip():
                raise ValueError("TEAMS_SECURITY_TOKEN es obligatorio en producción")
            if not self.llm_api_key.get_secret_value().strip():
                raise ValueError("LLM_API_KEY es obligatorio en producción")
            if self.llm_data_policy is None:
                raise ValueError("LLM_DATA_POLICY es obligatorio en producción")
            if not self.embedding_model_path.strip():
                raise ValueError("EMBEDDING_MODEL_PATH es obligatorio en producción")
            if not self.rerank_model_path.strip():
                raise ValueError("RERANK_MODEL_PATH es obligatorio en producción")

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Devuelve la configuración validada, cacheada por proceso."""

    return Settings()
