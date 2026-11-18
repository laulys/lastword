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
