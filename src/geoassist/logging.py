"""Logging estructurado en JSON con redacción de secretos.

La salida va a stdout y cada línea es un objeto JSON con timestamp UTC. Antes
de serializar, cualquier clave cuyo nombre contenga ``token``, ``secret``,
``api_key``, ``authorization`` o ``password`` se sustituye por ``***``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

REDACTED = "***"
SENSITIVE_KEY_PARTS = ("token", "secret", "api_key", "authorization", "password")


def _redact_value(key: str, value: Any) -> Any:
    if any(part in key.lower() for part in SENSITIVE_KEY_PARTS):
        return REDACTED

    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for item_key, item in value.items():
            key_text = str(item_key)
            redacted[key_text] = _redact_value(key_text, item)
        return redacted
    if isinstance(value, (list, tuple)):
        return [_redact_value("", item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_redact_value("", item) for item in sorted(value, key=str)]
    return value


def _redact_processor(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    del logger, method_name
    return {key: _redact_value(key, value) for key, value in event_dict.items()}


def _level_value(level: str) -> int:
    normalized = level.upper()
    levels = logging.getLevelNamesMapping()
    try:
        return levels[normalized]
    except KeyError:
        raise ValueError(f"nivel de log inválido: {level!r}") from None


class _DynamicPrintLogger(structlog.PrintLogger):
    """PrintLogger que resuelve ``sys.stdout`` en cada mensaje.

    A diferencia de ``PrintLogger(file=sys.stdout)``, no deja un stream fijo
    capturado al configurar: escribe siempre en el ``sys.stdout`` vigente.
    """

    def msg(self, message: str) -> None:
        print(message, flush=True)


class _DynamicPrintLoggerFactory:
    """Crea :class:`_DynamicPrintLogger` sin ligar ningún stream."""

    def __call__(self, *args: Any, **kwargs: Any) -> _DynamicPrintLogger:
        del args, kwargs
        return _DynamicPrintLogger()


def configure_logging(level: str) -> None:
    """Configura structlog para emitir JSON a stdout desde este momento."""

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact_processor,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(_level_value(level)),
        logger_factory=_DynamicPrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> Any:
    """Devuelve un logger estructurado con el nombre indicado."""

    return structlog.get_logger(name)
