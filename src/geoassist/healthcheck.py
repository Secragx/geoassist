"""Healthcheck para Docker sin depender de ``curl``.

Uso:
    python -m geoassist.healthcheck liveness
    python -m geoassist.healthcheck readiness

Consulta ``http://127.0.0.1:8000`` por defecto. El host puede cambiarse con
``--url`` o con la variable ``HEALTHCHECK_URL``. Sale con 0 si el endpoint
responde 2xx y con 1 en cualquier otro caso.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_SECONDS = 3.0
ENDPOINTS = {
    "liveness": "/health",
    "readiness": "/ready",
}


def _check(base_url: str, endpoint: str, timeout: float) -> bool:
    request = Request(
        f"{base_url.rstrip('/')}{endpoint}",
        headers={"User-Agent": "geoassist-healthcheck"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL interna
            return 200 <= response.status < 300
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        print(f"healthcheck {endpoint}: {exc}", file=sys.stderr)
        return False


def main(argv: Sequence[str] | None = None) -> int:
    """Interpreta el modo y devuelve el código de salida del healthcheck."""

    parser = argparse.ArgumentParser(prog="python -m geoassist.healthcheck")
    parser.add_argument("mode", choices=sorted(ENDPOINTS))
    parser.add_argument(
        "--url",
        default=os.environ.get("HEALTHCHECK_URL", DEFAULT_BASE_URL),
        help="URL base de la API (default: %(default)s)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Timeout en segundos (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    return 0 if _check(args.url, ENDPOINTS[args.mode], args.timeout) else 1


if __name__ == "__main__":
    raise SystemExit(main())
