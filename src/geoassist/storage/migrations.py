"""Aplicación de migraciones SQL versionadas sobre SQLite."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_MIGRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
)
"""


def _utc_now() -> str:
    """Devuelve la fecha actual como ISO-8601 UTC con sufijo ``Z``."""

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _iter_statements(script: str) -> Iterator[str]:
    """Divide un script SQL en sentencias completas.

    ``sqlite3.executescript`` confirma cualquier transacción pendiente antes de
    ejecutar, así que se ejecutan las sentencias una a una para poder envolver
    cada migración en una transacción atómica.
    """

    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                yield statement
            buffer = ""

    remainder = buffer.strip()
    if remainder and any(
        line.strip() and not line.strip().startswith("--") for line in remainder.splitlines()
    ):
        yield remainder


def apply_migrations(db_path: Path, migrations_dir: Path) -> None:
    """Aplica en orden los ``*.sql`` aún no registrados en ``schema_migrations``.

    Cada migración se ejecuta dentro de una transacción junto con su registro de
    versión. Las migraciones ya aplicadas se ignoran, por lo que la operación es
    idempotente.
    """

    db_path = Path(db_path)
    migrations_dir = Path(migrations_dir)

    if not migrations_dir.is_dir():
        raise FileNotFoundError(f"no existe el directorio de migraciones: {migrations_dir}")

    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        conn.isolation_level = None
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(SCHEMA_MIGRATIONS_SQL)

        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}

        for migration_path in sorted(migrations_dir.glob("*.sql"), key=lambda path: path.name):
            version = migration_path.name
            if version in applied:
                continue

            script = migration_path.read_text(encoding="utf-8")
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in _iter_statements(script):
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, _utc_now()),
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
    finally:
        conn.close()
