from pathlib import Path
from geoassist.storage.migrations import apply_migrations

ROOT = Path(__file__).resolve().parents[1]


def table_names(db_path):
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        ).fetchall()
    return {row[0] for row in rows}


def test_corpus_migrations_create_schema(tmp_path):
    db = tmp_path / "corpus.db"
    apply_migrations(db, ROOT / "migrations" / "corpus")
    assert {"documents", "chunks", "chunks_fts", "corpus_meta", "schema_migrations"} <= table_names(
        db
    )


def test_migrations_are_idempotent(tmp_path):
    db = tmp_path / "state.db"
    apply_migrations(db, ROOT / "migrations" / "state")
    apply_migrations(db, ROOT / "migrations" / "state")
    assert {"sessions", "messages", "activities", "feedback", "schema_migrations"} <= table_names(
        db
    )
