"""Limitación de tasa en memoria con ventana deslizante por clave."""

from __future__ import annotations

import time
from collections.abc import Callable


class RateLimiter:
    """Permite hasta ``limit`` solicitudes por ventana de ``window_seconds``."""

    def __init__(
        self,
        limit: int,
        window_seconds: int = 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._windows: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        """Indica si la solicitud actual entra en el límite de la clave."""

        now = self._clock()
        cutoff = now - self.window_seconds
        window: list[float] = [t for t in self._windows.get(key, []) if t > cutoff]

        if len(window) < self.limit:
            window.append(now)
            self._windows[key] = window
            return True

        self._windows[key] = window
        return False

    def __repr__(self) -> str:
        return (
            f"RateLimiter(limit={self.limit}, window_seconds={self.window_seconds}, "
            f"active_keys={len(self._windows)})"
        )


__all__: list[str] = ["RateLimiter"]
