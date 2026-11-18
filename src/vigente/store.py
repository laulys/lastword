"""Acceso a SQLite. Es el único módulo que escribe SQL.

Reglas:
- Toda escritura ocurre dentro de `transaction()`; `service.py` es quien las abre.
- Toda función que lee o escribe memorias exige `workspace_id`.
- Las fechas se guardan como milisegundos epoch UTC. «Sin fecha de fin» es `OPEN_END_MS`.
- Todo el SQL usa parámetros. Lo único que se interpola son constantes de este módulo.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from vigente.errors import SchemaVersionError
from vigente.models import Kind, Memory, Status

OPEN_END_MS = 253_402_300_799_999  # 9999-12-31T23:59:59.999Z
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_ONE_MS = timedelta(milliseconds=1)

_MIGRATIONS: tuple[str, ...] = (
    # v1: esquema inicial
    """
    CREATE TABLE workspaces (
        id         TEXT PRIMARY KEY,
        created_at INTEGER NOT NULL
    );

    CREATE TABLE memories (
        -- `seq` es un alias estable de rowid. VACUUM puede renumerar el rowid implícito
        -- de tablas sin INTEGER PRIMARY KEY, y el índice FTS se enlaza por este número.
        seq              INTEGER PRIMARY KEY,
        id               TEXT NOT NULL UNIQUE,
        workspace_id     TEXT NOT NULL REFERENCES workspaces(id),
        kind             TEXT NOT NULL CHECK (kind IN ('fact', 'episode')),
        content          TEXT NOT NULL CHECK (length(content) BETWEEN 1 AND 4000),
        tags             TEXT NOT NULL DEFAULT '[]',
        source           TEXT,
        confidence       REAL CHECK (confidence BETWEEN 0 AND 1),
        created_at       INTEGER NOT NULL,
        valid_from       INTEGER NOT NULL,
        valid_to         INTEGER NOT NULL DEFAULT 253402300799999,
        status           TEXT NOT NULL CHECK (status IN ('active', 'superseded')),
        supersedes_id    TEXT REFERENCES memories(id),
        superseded_by_id TEXT REFERENCES memories(id),
        supersede_reason TEXT,
        embedding_model  TEXT,
        CHECK (valid_from < valid_to),
        CHECK (kind = 'fact' OR (supersedes_id IS NULL AND superseded_by_id IS NULL))
    );
    CREATE INDEX memories_ws_status ON memories(workspace_id, status);
    CREATE INDEX memories_ws_validity ON memories(workspace_id, valid_from, valid_to);

    -- rowid = memories.seq. Sin stemming: el contenido mezcla español e inglés.
    CREATE VIRTUAL TABLE memories_fts USING fts5(
        content,
        tags,
        tokenize = 'unicode61 remove_diacritics 2'
    );
    """,
)
LATEST_SCHEMA_VERSION = len(_MIGRATIONS)

_COLUMN_NAMES = (
    "id",
    "workspace_id",
    "kind",
    "content",
    "tags",
    "source",
    "confidence",
    "created_at",
    "valid_from",
    "valid_to",
    "status",
    "supersedes_id",
    "superseded_by_id",
    "supersede_reason",
    "embedding_model",
)
_COLUMNS = ", ".join(_COLUMN_NAMES)
_M_COLUMNS = ", ".join(f"m.{name}" for name in _COLUMN_NAMES)
_PLACEHOLDERS = ", ".join("?" for _ in _COLUMN_NAMES)


# --- tiempo


def to_ms(value: datetime) -> int:
    """Milisegundos epoch UTC. Falla con fechas sin zona horaria a propósito."""
    return (value - _EPOCH) // _ONE_MS


def from_ms(ms: int) -> datetime:
    return _EPOCH + timedelta(milliseconds=ms)


# --- conexión, transacciones y migraciones


def connect(path: str | Path) -> sqlite3.Connection:
    in_memory = str(path) == ":memory:"
    target = ":memory:" if in_memory else Path(path).expanduser()
    if isinstance(target, Path):
        target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, autocommit=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA secure_delete = ON")  # lo borrado se sobrescribe con ceros
    if not in_memory:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    try:
        migrate(conn)
    except BaseException:
        conn.close()
        raise
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection, *, write: bool = True) -> Iterator[None]:
    """Una operación del usuario = una transacción. Si algo falla, no queda nada a medias."""
    conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _check_version(version: int) -> None:
    if version > LATEST_SCHEMA_VERSION:
        raise SchemaVersionError(
            f"La base usa el esquema v{version}, más nuevo que el que conoce esta versión "
            f"de Vigente (v{LATEST_SCHEMA_VERSION})."
        )


def migrate(conn: sqlite3.Connection) -> None:
    current = schema_version(conn)
    _check_version(current)
    if current == LATEST_SCHEMA_VERSION:
        return
    with transaction(conn):
        current = schema_version(conn)  # otro proceso pudo migrar mientras esperábamos
        _check_version(current)
        for version in range(current + 1, LATEST_SCHEMA_VERSION + 1):
            conn.executescript(_MIGRATIONS[version - 1])
        conn.execute(f"PRAGMA user_version = {LATEST_SCHEMA_VERSION}")


def checkpoint(conn: sqlite3.Connection) -> None:
    """Vuelca el WAL al archivo principal y lo vacía (mejor esfuerzo si hay lectores)."""
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


# --- escritura


def ensure_workspace(conn: sqlite3.Connection, workspace_id: str, created_ms: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO workspaces (id, created_at) VALUES (?, ?)",
        (workspace_id, created_ms),
    )


def insert_memory(conn: sqlite3.Connection, memory: Memory) -> None:
    cursor = conn.execute(
        f"INSERT INTO memories ({_COLUMNS}) VALUES ({_PLACEHOLDERS})",
        (
            memory.id,
            memory.workspace_id,
            memory.kind.value,
            memory.content,
            json.dumps(memory.tags, ensure_ascii=False),
            memory.source,
            memory.confidence,
            to_ms(memory.created_at),
            to_ms(memory.valid_from),
            OPEN_END_MS if memory.valid_to is None else to_ms(memory.valid_to),
            memory.status.value,
            memory.supersedes_id,
            memory.superseded_by_id,
            memory.supersede_reason,
            memory.embedding_model,
        ),
    )
    conn.execute(
        "INSERT INTO memories_fts (rowid, content, tags) VALUES (?, ?, ?)",
        (cursor.lastrowid, memory.content, " ".join(memory.tags)),
    )


def mark_superseded(
    conn: sqlite3.Connection,
    workspace_id: str,
    memory_id: str,
    *,
    valid_to: datetime,
    superseded_by_id: str,
) -> None:
    cursor = conn.execute(
        """
        UPDATE memories
        SET valid_to = ?, status = 'superseded', superseded_by_id = ?
        WHERE workspace_id = ? AND id = ? AND superseded_by_id IS NULL
        """,
        (to_ms(valid_to), superseded_by_id, workspace_id, memory_id),
    )
    if cursor.rowcount != 1:
        raise RuntimeError(f"No se pudo cerrar la vigencia de {memory_id}")


def delete_memories(conn: sqlite3.Connection, workspace_id: str, ids: Sequence[str]) -> int:
    """Borrado real: filas, índice de texto y, al compactar, los restos del índice."""
    if not ids:
        return 0
    marks = ", ".join("?" for _ in ids)
    seqs = [
        row[0]
        for row in conn.execute(
            f"SELECT seq FROM memories WHERE workspace_id = ? AND id IN ({marks})",
            (workspace_id, *ids),
        )
    ]
    conn.executemany("DELETE FROM memories_fts WHERE rowid = ?", [(seq,) for seq in seqs])
    cursor = conn.execute(
        f"DELETE FROM memories WHERE workspace_id = ? AND id IN ({marks})",
        (workspace_id, *ids),
    )
    # FTS5 marca los borrados y los aplica al fusionar segmentos. `optimize` fusiona ahora,
    # así los términos borrados no sobreviven en segmentos antiguos.
    conn.execute("INSERT INTO memories_fts (memories_fts) VALUES ('optimize')")
    return cursor.rowcount


# --- lectura


def _to_memory(row: sqlite3.Row) -> Memory:
    return Memory(
        id=row["id"],
        workspace_id=row["workspace_id"],
        kind=Kind(row["kind"]),
        content=row["content"],
        tags=json.loads(row["tags"]),
        source=row["source"],
        confidence=row["confidence"],
        created_at=from_ms(row["created_at"]),
        valid_from=from_ms(row["valid_from"]),
        valid_to=None if row["valid_to"] == OPEN_END_MS else from_ms(row["valid_to"]),
        status=Status(row["status"]),
        supersedes_id=row["supersedes_id"],
        superseded_by_id=row["superseded_by_id"],
        supersede_reason=row["supersede_reason"],
        embedding_model=row["embedding_model"],
    )


def get_memory(conn: sqlite3.Connection, workspace_id: str, memory_id: str) -> Memory | None:
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM memories WHERE workspace_id = ? AND id = ?",
        (workspace_id, memory_id),
    ).fetchone()
    return None if row is None else _to_memory(row)


def fts_search(
    conn: sqlite3.Connection,
    workspace_id: str,
    match: str,
    *,
    limit: int,
    at_ms: int | None,
    kind: Kind | None = None,
    exclude_id: str | None = None,
) -> list[tuple[Memory, float]]:
    """Busca con BM25. `at_ms` = solo lo vigente en ese instante; None = todas las versiones.

    Devuelve (memoria, bm25). En FTS5, bm25 es negativo y menor = más relevante.
    """
    sql = [
        f"SELECT {_M_COLUMNS}, bm25(memories_fts, 1.0, 0.5) AS rank",
        "FROM memories_fts JOIN memories AS m ON m.seq = memories_fts.rowid",
        "WHERE memories_fts MATCH ? AND m.workspace_id = ?",
    ]
    params: list[Any] = [match, workspace_id]
    if at_ms is not None:
        sql.append("AND m.valid_from <= ? AND m.valid_to > ?")
        params += [at_ms, at_ms]
    if kind is not None:
        sql.append("AND m.kind = ?")
        params.append(kind.value)
    if exclude_id is not None:
        sql.append("AND m.id != ?")
        params.append(exclude_id)
    sql.append("ORDER BY rank LIMIT ?")
    params.append(limit)
    rows = conn.execute(" ".join(sql), params).fetchall()
    return [(_to_memory(row), float(row["rank"])) for row in rows]


def list_memories(
    conn: sqlite3.Connection,
    workspace_id: str,
    *,
    kind: Kind | None,
    status: Status | None,
    tag: str | None,
    limit: int,
    before_seq: int | None,
) -> tuple[list[Memory], int | None]:
    """Más recientes primero. Devuelve también el cursor de la página siguiente."""
    sql = [f"SELECT seq, {_COLUMNS} FROM memories WHERE workspace_id = ?"]
    params: list[Any] = [workspace_id]
    if kind is not None:
        sql.append("AND kind = ?")
        params.append(kind.value)
    if status is not None:
        sql.append("AND status = ?")
        params.append(status.value)
    if tag is not None:
        sql.append("AND EXISTS (SELECT 1 FROM json_each(memories.tags) WHERE value = ?)")
        params.append(tag)
    if before_seq is not None:
        sql.append("AND seq < ?")
        params.append(before_seq)
    sql.append("ORDER BY seq DESC LIMIT ?")
    params.append(limit + 1)
    rows = conn.execute(" ".join(sql), params).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_seq = int(rows[-1]["seq"]) if has_more else None
    return [_to_memory(row) for row in rows], next_seq


def stats(conn: sqlite3.Connection, workspace_id: str) -> dict[str, int]:
    counts = {"facts_active": 0, "facts_superseded": 0, "episodes": 0}
    rows = conn.execute(
        "SELECT kind, status, count(*) FROM memories WHERE workspace_id = ? GROUP BY kind, status",
        (workspace_id,),
    )
    for kind, status, count in rows:
        key = "episodes" if kind == Kind.EPISODE.value else f"facts_{status}"
        counts[key] += int(count)
    counts["total"] = sum(counts.values())
    return counts


# --- integridad

_BROKEN_CHAINS_SQL = """
SELECT m.id FROM memories AS m
LEFT JOIN memories AS nxt ON nxt.id = m.superseded_by_id
LEFT JOIN memories AS prv ON prv.id = m.supersedes_id
WHERE (m.superseded_by_id IS NULL AND (m.status != 'active' OR m.valid_to != ?))
   OR (m.superseded_by_id IS NOT NULL AND (
          nxt.id IS NULL
          OR nxt.supersedes_id IS NOT m.id
          OR nxt.valid_from != m.valid_to
          OR nxt.workspace_id != m.workspace_id
          OR m.status != 'superseded'))
   OR (m.supersedes_id IS NOT NULL AND (prv.id IS NULL OR prv.superseded_by_id IS NOT m.id))
ORDER BY m.seq
"""


def _scalar(conn: sqlite3.Connection, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0])


def integrity_problems(conn: sqlite3.Connection) -> list[str]:
    """Comprueba las invariantes que el resto del código da por ciertas."""
    problems: list[str] = []
    quick = conn.execute("PRAGMA quick_check").fetchone()[0]
    if quick != "ok":
        problems.append(f"SQLite quick_check: {quick}")
    broken_refs = conn.execute("PRAGMA foreign_key_check").fetchall()
    if broken_refs:
        problems.append(f"{len(broken_refs)} referencias rotas entre memorias")
    try:
        conn.execute("INSERT INTO memories_fts (memories_fts) VALUES ('integrity-check')")
    except sqlite3.DatabaseError as exc:
        problems.append(f"índice de texto dañado: {exc}")
    orphans = _scalar(
        conn, "SELECT count(*) FROM memories_fts WHERE rowid NOT IN (SELECT seq FROM memories)"
    )
    if orphans:
        problems.append(f"{orphans} entradas del índice de texto sin memoria")
    missing = _scalar(
        conn, "SELECT count(*) FROM memories WHERE seq NOT IN (SELECT rowid FROM memories_fts)"
    )
    if missing:
        problems.append(f"{missing} memorias sin entrada en el índice de texto")
    for (memory_id,) in conn.execute(_BROKEN_CHAINS_SQL, (OPEN_END_MS,)):
        problems.append(f"cadena de reemplazos inconsistente en {memory_id}")
    return problems
