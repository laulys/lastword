from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.helpers import T0
from vigente import store
from vigente.errors import SchemaVersionError
from vigente.models import Kind, Memory


def test_connect_creates_schema_and_safety_pragmas(conn: sqlite3.Connection) -> None:
    assert store.schema_version(conn) == store.LATEST_SCHEMA_VERSION
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA secure_delete").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_reconnecting_keeps_the_schema(db_path: Path) -> None:
    store.connect(db_path).close()
    conn = store.connect(db_path)
    assert store.schema_version(conn) == store.LATEST_SCHEMA_VERSION
    conn.close()


def test_refuses_a_schema_newer_than_the_code(db_path: Path) -> None:
    conn = store.connect(db_path)
    conn.execute("PRAGMA user_version = 999")
    conn.close()
    with pytest.raises(SchemaVersionError):
        store.connect(db_path)


def test_failed_transaction_leaves_no_rows_and_no_text_index(conn: sqlite3.Connection) -> None:
    memory = Memory(
        id="01JD0000000000000000000000",
        workspace_id="test",
        kind=Kind.FACT,
        content="Esto no debería quedar guardado",
        tags=[],
        created_at=T0,
        valid_from=T0,
    )
    with pytest.raises(RuntimeError), store.transaction(conn):
        store.ensure_workspace(conn, "test", 0)
        store.insert_memory(conn, memory)
        raise RuntimeError("fallo simulado")
    for table in ("workspaces", "memories", "memories_fts"):
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_milliseconds_roundtrip() -> None:
    value = datetime(2026, 3, 1, 12, 30, 15, 123_456, tzinfo=UTC)
    assert store.from_ms(store.to_ms(value)) == value.replace(microsecond=123_000)


def test_other_timezones_are_converted_to_utc() -> None:
    madrid = timezone(timedelta(hours=1))
    assert store.to_ms(datetime(2026, 3, 1, 13, 30, tzinfo=madrid)) == store.to_ms(
        datetime(2026, 3, 1, 12, 30, tzinfo=UTC)
    )


def test_naive_datetimes_are_a_bug() -> None:
    with pytest.raises(TypeError):
        store.to_ms(datetime(2026, 3, 1))


def test_upgrades_a_database_from_the_previous_schema(db_path: Path) -> None:
    old = sqlite3.connect(db_path, autocommit=True)
    old.executescript(store._MIGRATIONS[0])
    old.execute("PRAGMA user_version = 1")
    old.execute("INSERT INTO workspaces (id, created_at) VALUES ('test', 0)")
    old.close()
    conn = store.connect(db_path)
    assert store.schema_version(conn) == store.LATEST_SCHEMA_VERSION == 2
    assert store.embedding_spaces(conn) == []
    assert [tuple(row) for row in conn.execute("SELECT id FROM workspaces")] == [("test",)]
    conn.close()


def test_text_search_scans_the_fts_index_first(conn: sqlite3.Connection) -> None:
    """Si SQLite recorre `memories` por fuera, ejecuta la búsqueda de texto una vez por fila
    (coste cuadrático). El plan debe empezar por el índice FTS."""
    captured: list[str] = []
    conn.set_trace_callback(captured.append)
    store.fts_search(conn, "test", '"ana"', limit=5, at_ms=0, kind=Kind.FACT, exclude_id="x")
    conn.set_trace_callback(None)
    query = next(sql for sql in captured if "memories_fts MATCH" in sql)
    plan = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + query)]
    assert plan[0].startswith("SCAN memories_fts"), plan
