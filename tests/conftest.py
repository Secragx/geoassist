"""Configuración común de pytest.

Permite ejecutar las pruebas desde un clon limpio aunque el paquete no esté
instalado: `src/` queda disponible para importar `geoassist` y la raíz del
proyecto para importar `tests.factories`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _ensure_on_path(path: Path) -> None:
    entry = str(path)
    if entry not in sys.path:
        sys.path.insert(0, entry)


if importlib.util.find_spec("geoassist") is None:
    _ensure_on_path(SRC)

# La raíz siempre es necesaria para `tests.factories` cuando no se usan
# importaciones relativas.
_ensure_on_path(ROOT)
