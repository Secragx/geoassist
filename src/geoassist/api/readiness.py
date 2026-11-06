"""Sonda de readiness con detalle por comprobación."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from geoassist.logging import get_logger

logger = get_logger(__name__)

ReadinessCheck = Callable[[], bool]


class ReadinessProbe:
    """Ejecuta comprobaciones independientes y reporta el resultado de cada una.

    Una comprobación que lanza una excepción se considera fallida: la sonda
    nunca propaga el error porque su contrato es devolver un estado y un
    detalle serializable.
    """

    def __init__(self, checks: Mapping[str, ReadinessCheck]) -> None:
        self._checks: dict[str, ReadinessCheck] = dict(checks)

    @property
    def checks(self) -> Mapping[str, ReadinessCheck]:
        """Devuelve una vista inmutable de las comprobaciones registradas."""

        return dict(self._checks)

    def run(self) -> tuple[bool, dict[str, bool]]:
        """Ejecuta todas las comprobaciones y devuelve ``(ready, detalle)``."""

        results: dict[str, bool] = {}
        for name, check in self._checks.items():
            try:
                results[name] = bool(check())
            except Exception as exc:  # noqa: BLE001 - la sonda debe degradar, no romper
                logger.warning(
                    "readiness_check_failed",
                    check=name,
                    error=type(exc).__name__,
                )
                results[name] = False

        return all(results.values()), results
