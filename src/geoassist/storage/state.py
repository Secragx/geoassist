"""Almacén de estado: sesiones de chat, historial y caché de actividades.

La capa de aplicación es responsable de la purga por TTL; el esquema guarda
fechas ISO-8601 en UTC para que la comparación sea determinista.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from geoassist.models import ChatMessage, SessionState
from geoassist.storage.migrations import apply_migrations

_MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations" / "state"

_SESSION_TTL = timedelta(minutes=120)
_ACTIVITY_TTL = timedelta(hours=24)
_MAX_MESSAGES = 12


def _utc_now() -> str:
    """Devuelve la fecha actual como ISO-8601 UTC con sufijo ``Z``."""

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _isoformat(dt: datetime) -> str:
    """Serializa una fecha consciente de zona a ISO-8601 UTC con sufijo ``Z``."""

    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    """Parsea una fecha ISO-8601, aceptando tanto ``Z`` como ``+00:00``."""

    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def session_key(tenant_id: str | None, conversation_id: str, user_id: str) -> str:
    """Devuelve la clave canónica de una sesión: tenant|conversation|user."""

    return f"{tenant_id or 'default'}|{conversation_id}|{user_id}"


class StateStore:
    """Persistencia SQLite de sesiones, historial de mensajes y caché de actividades."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        apply_migrations(self._db_path, _MIGRATIONS_DIR)

    def _connect(self) -> sqlite3.Connection:
        """Abre una conexión con WAL y claves foráneas activadas."""

        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def get_session(self, key: str, now: datetime) -> SessionState:
        """Devuelve el estado de una sesión; historial vacío si expiró o no existe."""

        with self._connect() as conn:
            row = conn.execute(
                "SELECT app_hint, last_seen_at FROM sessions WHERE session_key = ?",
                (key,),
            ).fetchone()

        if row is None:
            return SessionState(session_key=key, app_hint=None, history=())

        last_seen_at = _parse_iso(row[1])
        if now - last_seen_at > _SESSION_TTL:
            return SessionState(session_key=key, app_hint=None, history=())

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT role, content, created_at
                FROM messages
                WHERE session_key = ?
                ORDER BY created_at ASC, id ASC
                """,
                (key,),
            ).fetchall()

        history = tuple(
            ChatMessage(role=role, content=content, created_at=created_at)
            for role, content, created_at in rows
        )
        return SessionState(session_key=key, app_hint=row[0], history=history)

    def append_turn(
        self,
        session_key: str,
        *,
        tenant_id: str | None,
        conversation_id: str,
        user_id: str,
        question: str,
        answer: str,
        sources: list[dict] | tuple[dict, ...],
        app_hint: str | None,
        now: datetime,
    ) -> None:
        """Inserta un par de mensajes (usuario/asisente) y actualiza la sesión."""

        created_at = _isoformat(now)
        tenant = tenant_id or "default"
        sources_json = json.dumps(list(sources))

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    session_key, tenant_id, conversation_id, user_id,
                    app_hint, created_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_key) DO UPDATE SET
                    app_hint = excluded.app_hint,
                    last_seen_at = excluded.last_seen_at
                """,
                (session_key, tenant, conversation_id, user_id, app_hint, created_at, created_at),
            )

            conn.executemany(
                """
                INSERT INTO messages (session_key, role, content, sources, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (session_key, "user", question, sources_json, created_at),
                    (session_key, "assistant", answer, sources_json, created_at),
                ],
            )

            conn.execute(
                """
                DELETE FROM messages
                WHERE session_key = ?
                  AND id NOT IN (
                      SELECT id FROM messages
                      WHERE session_key = ?
                      ORDER BY created_at DESC, id DESC
                      LIMIT ?
                  )
                """,
                (session_key, session_key, _MAX_MESSAGES),
            )

            conn.commit()

    def get_cached_response(self, activity_id: str, now: datetime) -> dict | None:
        """Devuelve la respuesta cacheada de una actividad o ``None`` si expiró."""

        with self._connect() as conn:
            row = conn.execute(
                "SELECT response, created_at FROM activities WHERE activity_id = ?",
                (activity_id,),
            ).fetchone()

        if row is None:
            return None

        created_at = _parse_iso(row[1])
        if now - created_at > _ACTIVITY_TTL:
            return None

        return json.loads(row[0])

    def cache_response(self, activity_id: str, response: dict, now: datetime) -> None:
        """Guarda la respuesta formateada de una actividad para idempotencia."""

        created_at = _isoformat(now)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO activities (activity_id, response, created_at) VALUES (?, ?, ?)",
                (activity_id, json.dumps(response), created_at),
            )
            conn.commit()

    def purge_expired(self, now: datetime) -> None:
        """Elimina sesiones, mensajes y actividades cuyo TTL haya vencido."""

        session_cutoff = _isoformat(now - _SESSION_TTL)
        activity_cutoff = _isoformat(now - _ACTIVITY_TTL)

        with self._connect() as conn:
            conn.execute(
                """
                DELETE FROM messages
                WHERE session_key IN (
                    SELECT session_key FROM sessions WHERE last_seen_at < ?
                )
                """,
                (session_cutoff,),
            )
            conn.execute(
                "DELETE FROM sessions WHERE last_seen_at < ?",
                (session_cutoff,),
            )
            conn.execute(
                "DELETE FROM activities WHERE created_at < ?",
                (activity_cutoff,),
            )
            conn.commit()
