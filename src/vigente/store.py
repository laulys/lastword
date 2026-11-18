"""Acceso a SQLite. Es el único módulo que escribe SQL.

Reglas:
- Toda escritura ocurre dentro de `transaction()`; `service.py` es quien las abre.
- Toda función que lee o escribe memorias exige `workspace_id`.
- Las fechas se guardan como milisegundos epoch UTC. «Sin fecha de fin» es `OPEN_END_MS`.
- Todo el SQL usa parámetros. Lo único que se interpola son constantes de este módulo.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import sqlite_vec

from vigente.errors import ConfigError, DatabaseError, SchemaVersionError
from vigente.i18n import tr
from vigente.models import Kind, Memory, Status

OPEN_END_MS = 253_402_300_799_999  # 9999-12-31T23:59:59.999Z
BUSY_TIMEOUT_MS = 5000  # cuánto espera una conexión a que otra suelte la base
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_ONE_MS = timedelta(milliseconds=1)

_MIGRATIONS: tuple[str | Callable[[sqlite3.Connection], None], ...] = (
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
    # v2: registro de espacios vectoriales. La tabla vec0 de cada espacio se crea al guardar
    # su primer vector, porque su dimensión depende del modelo (ver ensure_embedding_space).
    """
    CREATE TABLE embedding_spaces (
        id         INTEGER PRIMARY KEY,
        model      TEXT NOT NULL,
        dim        INTEGER NOT NULL CHECK (dim BETWEEN 1 AND 8192),
        table_name TEXT NOT NULL UNIQUE,
        created_at INTEGER NOT NULL,
        UNIQUE (model, dim)
    );
    """,
    # v3: un hecho nuevo puede reemplazar a varios (ADR-0008). Cada versión anterior apunta a
    # su sucesora con superseded_by_id; este índice encuentra todas las de una versión nueva.
    """
    CREATE INDEX memories_superseded_by ON memories(superseded_by_id)
        WHERE superseded_by_id IS NOT NULL;
    """,
    # v4: búsqueda en dos pasos (ADR-0010). Se define más abajo: necesita Python.
    lambda conn: _two_stage_vectors(conn),
    # v5: saber si a alguna memoria le falta el vector del modelo en uso se pregunta en cada
    # búsqueda y al guardar. Sin índice recorría la tabla: 7 ms con 50.000 memorias.
    """
    CREATE INDEX memories_ws_embedding ON memories(workspace_id, embedding_model);
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


def _load_sqlite_vec(conn: sqlite3.Connection) -> None:
    if not hasattr(conn, "enable_load_extension"):
        raise ConfigError(
            tr(
                "Este Python no permite cargar extensiones de SQLite y Vigente necesita "
                "sqlite-vec. Usa un Python gestionado por uv."
            )
        )
    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)


def connect(path: str | Path) -> sqlite3.Connection:
    in_memory = str(path) == ":memory:"
    target = ":memory:" if in_memory else Path(path).expanduser()
    if isinstance(target, Path):
        # La memoria es privada: la carpeta y el archivo nacen legibles solo para su dueño.
        # SQLite crea después el WAL con los mismos permisos que el archivo.
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not target.exists() and target.parent.is_dir():
            os.close(os.open(target, os.O_CREAT | os.O_RDWR, 0o600))
    conn = sqlite3.connect(target, autocommit=True)
    conn.row_factory = sqlite3.Row
    try:
        _load_sqlite_vec(conn)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA secure_delete = ON")  # lo borrado se sobrescribe con ceros
        if not in_memory:
            _enable_wal(conn)
            conn.execute("PRAGMA synchronous = NORMAL")
        migrate(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def _enable_wal(conn: sqlite3.Connection) -> None:
    """Activa WAL, reintentando mientras otro proceso tenga la base bloqueada.

    Cuando varios clientes arrancan a la vez su servidor sobre una base nueva, todos
    intentan pasarla a WAL. SQLite no aplica `busy_timeout` a ese cambio y responde
    «database is locked» al momento, así que aquí se espera con el mismo plazo.
    """
    deadline = time.monotonic() + BUSY_TIMEOUT_MS / 1000
    pause = 0.005
    while True:
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as exc:
            busy = exc.sqlite_errorcode & 0xFF == sqlite3.SQLITE_BUSY
            if not busy or time.monotonic() >= deadline:
                raise
        time.sleep(pause)
        pause = min(pause * 2, 0.1)


def database_error(exc: sqlite3.Error) -> DatabaseError:
    """Un fallo de SQLite, contado a quien usa la CLI o el servidor en vez de volcarlo."""
    code = getattr(exc, "sqlite_errorcode", None)
    if code is not None and code & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
        return DatabaseError(
            tr("La base de datos está ocupada por otro proceso. Vuelve a intentarlo.")
        )
    return DatabaseError(tr("Error de la base de datos: {error}", error=exc))


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
            tr(
                "La base usa el esquema v{found}, más nuevo que el que conoce esta versión de "
                "Vigente (v{known}).",
                found=version,
                known=LATEST_SCHEMA_VERSION,
            )
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
            step = _MIGRATIONS[version - 1]
            if isinstance(step, str):
                conn.executescript(step)
            else:
                step(conn)
        conn.execute(f"PRAGMA user_version = {LATEST_SCHEMA_VERSION}")


def checkpoint(conn: sqlite3.Connection) -> None:
    """Vuelca el WAL al archivo principal y lo vacía (mejor esfuerzo si hay lectores)."""
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


# --- escritura


def workspace_exists(conn: sqlite3.Connection, workspace_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
    return row is not None


def ensure_workspace(conn: sqlite3.Connection, workspace_id: str, created_ms: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO workspaces (id, created_at) VALUES (?, ?)",
        (workspace_id, created_ms),
    )


def insert_memory(conn: sqlite3.Connection, memory: Memory) -> int:
    """Inserta la fila y su entrada FTS. Devuelve `seq`, que enlaza los índices."""
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
            _valid_to_ms(memory),
            memory.status.value,
            memory.supersedes_id,
            memory.superseded_by_id,
            memory.supersede_reason,
            memory.embedding_model,
        ),
    )
    seq = cursor.lastrowid
    if seq is None:
        raise RuntimeError("SQLite no devolvió el número de fila de la memoria")
    conn.execute(
        "INSERT INTO memories_fts (rowid, content, tags) VALUES (?, ?, ?)",
        (seq, memory.content, " ".join(memory.tags)),
    )
    return seq


def set_supersedes(
    conn: sqlite3.Connection,
    workspace_id: str,
    memory_id: str,
    *,
    supersedes_id: str,
    reason: str | None,
) -> None:
    """Marca una memoria ya guardada como versión nueva de otra."""
    cursor = conn.execute(
        "UPDATE memories SET supersedes_id = ?, supersede_reason = ? "
        "WHERE workspace_id = ? AND id = ? AND supersedes_id IS NULL",
        (supersedes_id, reason, workspace_id, memory_id),
    )
    if cursor.rowcount != 1:
        raise RuntimeError(f"No se pudo enlazar {memory_id} como versión nueva de {supersedes_id}")


def mark_superseded(
    conn: sqlite3.Connection,
    workspace_id: str,
    memory_id: str,
    *,
    valid_to: datetime,
    superseded_by_id: str,
) -> None:
    rows = conn.execute(
        """
        UPDATE memories
        SET valid_to = ?, status = 'superseded', superseded_by_id = ?
        WHERE workspace_id = ? AND id = ? AND superseded_by_id IS NULL
        RETURNING seq
        """,
        (to_ms(valid_to), superseded_by_id, workspace_id, memory_id),
    ).fetchall()
    if len(rows) != 1:
        raise RuntimeError(f"No se pudo cerrar la vigencia de {memory_id}")
    # Los índices vectoriales filtran por fechas dentro del KNN: hay que mantenerlas al día.
    for space in embedding_spaces(conn):
        conn.execute(
            f"UPDATE {space.table} SET valid_to = ? WHERE rowid = ?",
            (to_ms(valid_to), rows[0][0]),
        )


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
    for space in embedding_spaces(conn):
        conn.executemany(f"DELETE FROM {space.table} WHERE rowid = ?", [(seq,) for seq in seqs])
        conn.executemany(f"DELETE FROM {space.exact} WHERE seq = ?", [(seq,) for seq in seqs])
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


def predecessor_ids(conn: sqlite3.Connection, workspace_id: str, memory_id: str) -> list[str]:
    """Las versiones que `memory_id` reemplazó: una, o varias si unió varios hechos."""
    rows = conn.execute(
        "SELECT id FROM memories WHERE workspace_id = ? AND superseded_by_id = ? "
        "ORDER BY valid_from, seq",
        (workspace_id, memory_id),
    ).fetchall()
    return [str(row["id"]) for row in rows]


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
        # CROSS JOIN obliga a recorrer primero el índice FTS. Con un JOIN normal, SQLite puede
        # elegir recorrer `memories` por su índice y ejecutar la búsqueda de texto una vez por
        # fila: coste cuadrático (432 ms frente a 1,9 ms con 2.000 memorias; ver eval/scale.py).
        "FROM memories_fts CROSS JOIN memories AS m ON m.seq = memories_fts.rowid",
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


def get_memory_by_seq(conn: sqlite3.Connection, workspace_id: str, seq: int) -> Memory | None:
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM memories WHERE workspace_id = ? AND seq = ?",
        (workspace_id, seq),
    ).fetchone()
    return None if row is None else _to_memory(row)


def _valid_to_ms(memory: Memory) -> int:
    return OPEN_END_MS if memory.valid_to is None else to_ms(memory.valid_to)


# --- vectores

_VECTOR_TABLE = re.compile(r"^vecb_\d+$")
MAX_DIM = 8192
RERANK_MIN = 100  # candidatos que se reordenan como mínimo (ADR-0010)


@dataclass(frozen=True)
class EmbeddingSpace:
    """Un índice vectorial por (modelo, dimensión): nunca se mezclan vectores incompatibles."""

    id: int
    model: str
    dim: int
    table: str  # vectores binarios en sqlite-vec, con los filtros: elige candidatos

    @property
    def exact(self) -> str:
        """Vectores exactos en una tabla normal: reordenan los candidatos."""
        return f"vecx_{self.id}"


def _to_space(row: sqlite3.Row) -> EmbeddingSpace:
    table = str(row["table_name"])
    if not _VECTOR_TABLE.match(table):  # se interpola en SQL: defensa adicional
        raise RuntimeError(f"Nombre de tabla vectorial inesperado: {table!r}")
    return EmbeddingSpace(
        id=int(row["id"]), model=str(row["model"]), dim=int(row["dim"]), table=table
    )


def embedding_spaces(conn: sqlite3.Connection) -> list[EmbeddingSpace]:
    rows = conn.execute("SELECT id, model, dim, table_name FROM embedding_spaces ORDER BY id")
    return [_to_space(row) for row in rows]


def find_embedding_space(
    conn: sqlite3.Connection, model: str, dim: int | None = None
) -> EmbeddingSpace | None:
    """El espacio de un modelo (y dimensión, si se indica). Si hay varios, el más reciente."""
    sql = "SELECT id, model, dim, table_name FROM embedding_spaces WHERE model = ?"
    params: list[Any] = [model]
    if dim is not None:
        sql += " AND dim = ?"
        params.append(dim)
    row = conn.execute(sql + " ORDER BY id DESC LIMIT 1", params).fetchone()
    return None if row is None else _to_space(row)


def ensure_embedding_space(
    conn: sqlite3.Connection, model: str, dim: int, created_ms: int
) -> EmbeddingSpace:
    existing = find_embedding_space(conn, model, dim)
    if existing is not None:
        return existing
    if not 1 <= dim <= MAX_DIM:
        raise ValueError(f"Dimensión de embedding fuera de rango: {dim}")
    space_id = _scalar(conn, "SELECT coalesce(max(id), 0) + 1 FROM embedding_spaces")
    table = f"vecb_{space_id}"
    conn.execute(
        "INSERT INTO embedding_spaces (id, model, dim, table_name, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (space_id, model, dim, table, created_ms),
    )
    _create_vector_tables(conn, space_id, dim)
    return EmbeddingSpace(id=space_id, model=model, dim=dim, table=table)


def _bits(dim: int) -> int:
    """sqlite-vec cuantiza en bloques de 8 dimensiones: se rellena con ceros."""
    return -(-dim // 8) * 8


def _create_vector_tables(conn: sqlite3.Connection, space_id: int, dim: int) -> None:
    # workspace_id como partition key y fechas/tipo como metadatos: así los filtros se
    # aplican dentro del KNN y no hace falta sobremuestrear (comprobado en el hito 0).
    conn.execute(
        f"""
        CREATE VIRTUAL TABLE vecb_{space_id} USING vec0(
            workspace_id TEXT PARTITION KEY,
            kind         TEXT,
            valid_from   INTEGER,
            valid_to     INTEGER,
            embedding    BIT[{_bits(dim)}]
        )
        """
    )
    # Leer 100 vectores por rowid de sqlite-vec cuesta 15 ms; de una tabla normal, 0,1 ms.
    conn.execute(
        f"CREATE TABLE vecx_{space_id} ("
        "seq INTEGER PRIMARY KEY REFERENCES memories(seq), embedding BLOB NOT NULL)"
    )


def _two_stage_vectors(conn: sqlite3.Connection) -> None:
    """v4: cada espacio pasa de una tabla de vectores exactos en sqlite-vec a dos: binarios
    para elegir candidatos y exactos para reordenarlos. Se conservan todos los vectores."""
    for row in conn.execute("SELECT id, dim, table_name FROM embedding_spaces").fetchall():
        old = str(row["table_name"])
        if not re.fullmatch(r"vec_\d+", old):  # se interpola en SQL
            raise RuntimeError(f"Nombre de tabla vectorial inesperado: {old!r}")
        space_id, dim = int(row["id"]), int(row["dim"])
        _create_vector_tables(conn, space_id, dim)
        padding = sqlite_vec.serialize_float32([0.0] * (_bits(dim) - dim))
        rows = conn.execute(
            f"SELECT rowid, workspace_id, kind, valid_from, valid_to, embedding FROM {old}"
        ).fetchall()
        for seq, workspace_id, kind, valid_from, valid_to, embedding in rows:
            conn.execute(
                f"INSERT INTO vecx_{space_id} (seq, embedding) VALUES (?, ?)", (seq, embedding)
            )
            conn.execute(
                f"INSERT INTO vecb_{space_id} "
                "(rowid, workspace_id, kind, valid_from, valid_to, embedding) "
                "VALUES (?, ?, ?, ?, ?, vec_quantize_binary(?))",
                (seq, workspace_id, kind, valid_from, valid_to, bytes(embedding) + padding),
            )
        conn.execute(f"DROP TABLE {old}")
        conn.execute(
            "UPDATE embedding_spaces SET table_name = ? WHERE id = ?",
            (f"vecb_{space_id}", space_id),
        )


def insert_vector(
    conn: sqlite3.Connection,
    space: EmbeddingSpace,
    seq: int,
    memory: Memory,
    vector: Sequence[float],
) -> None:
    if len(vector) != space.dim:
        raise ValueError(f"El vector tiene {len(vector)} dimensiones y el espacio, {space.dim}")
    exact = sqlite_vec.serialize_float32(list(vector))
    padded = sqlite_vec.serialize_float32([*vector, *([0.0] * (_bits(space.dim) - space.dim))])
    conn.execute(
        f"INSERT INTO {space.table} (rowid, workspace_id, kind, valid_from, valid_to, embedding) "
        "VALUES (?, ?, ?, ?, ?, vec_quantize_binary(?))",
        (
            seq,
            memory.workspace_id,
            memory.kind.value,
            to_ms(memory.valid_from),
            _valid_to_ms(memory),
            padded,
        ),
    )
    conn.execute(f"INSERT INTO {space.exact} (seq, embedding) VALUES (?, ?)", (seq, exact))
    conn.execute("UPDATE memories SET embedding_model = ? WHERE seq = ?", (space.model, seq))


def vector_search(
    conn: sqlite3.Connection,
    space: EmbeddingSpace,
    workspace_id: str,
    vector: Sequence[float],
    *,
    limit: int,
    at_ms: int | None,
    kind: Kind | None = None,
) -> list[tuple[Memory, float]]:
    """KNN en dos pasos (ADR-0010). Devuelve (memoria, distancia coseno exacta).

    1. KNN sobre los vectores binarios, con los filtros dentro, para elegir candidatos.
    2. Reordenación de los candidatos con los vectores exactos.

    Si los filtros dejan menos de `RERANK_MIN` memorias, entran todas y el resultado es exacto.
    Un vector guardado todo a ceros no tiene distancia coseno: se queda fuera, no rompe la búsqueda.
    """
    query = list(vector)
    exact = sqlite_vec.serialize_float32(query)
    padded = sqlite_vec.serialize_float32([*query, *([0.0] * (_bits(space.dim) - space.dim))])
    where = ["embedding MATCH vec_quantize_binary(?)", "k = ?", "workspace_id = ?"]
    params: list[Any] = [padded, max(RERANK_MIN, 5 * limit), workspace_id]
    if at_ms is not None:
        where.append("valid_from <= ? AND valid_to > ?")
        params += [at_ms, at_ms]
    if kind is not None:
        where.append("kind = ?")
        params.append(kind.value)
    params += [exact, limit, workspace_id]
    sql = f"""
        WITH candidates AS (SELECT rowid FROM {space.table} WHERE {" AND ".join(where)}),
        ranked AS (
            SELECT x.seq AS seq, vec_distance_cosine(x.embedding, ?) AS distance
            FROM candidates JOIN {space.exact} AS x ON x.seq = candidates.rowid
            ORDER BY distance IS NULL, distance LIMIT ?
        )
        SELECT {_M_COLUMNS}, ranked.distance AS distance
        FROM ranked JOIN memories AS m ON m.seq = ranked.seq
        WHERE m.workspace_id = ? AND ranked.distance IS NOT NULL
        ORDER BY ranked.distance
    """
    rows = conn.execute(sql, params).fetchall()
    return [(_to_memory(row), float(row["distance"])) for row in rows]


def memories_without_vector(
    conn: sqlite3.Connection, workspace_id: str, space: EmbeddingSpace | None, *, limit: int
) -> list[tuple[int, Memory]]:
    sql = f"SELECT seq, {_COLUMNS} FROM memories WHERE workspace_id = ?"
    if space is not None:
        sql += f" AND seq NOT IN (SELECT seq FROM {space.exact})"
    rows = conn.execute(sql + " ORDER BY seq LIMIT ?", (workspace_id, limit)).fetchall()
    return [(int(row["seq"]), _to_memory(row)) for row in rows]


def count_without_vector(
    conn: sqlite3.Connection, workspace_id: str, space: EmbeddingSpace | None
) -> int:
    sql = "SELECT count(*) FROM memories WHERE workspace_id = ?"
    if space is not None:
        sql += f" AND seq NOT IN (SELECT seq FROM {space.exact})"
    return int(conn.execute(sql, (workspace_id,)).fetchone()[0])


def has_memories_without_vector(
    conn: sqlite3.Connection, workspace_id: str, space: EmbeddingSpace
) -> bool:
    """¿Alguna memoria sin vector en `space`? (Guardada con el servicio caído, o con otro modelo.)

    `memories.embedding_model` es el modelo del último vector de cada memoria. Si la base solo
    tiene un espacio vectorial, basta con esa columna, que tiene índice. Con varios (se cambió de
    modelo, o el modelo cambió de dimensión) la columna no dice en qué espacios hay vector, y
    se comprueba en el propio espacio, que es más lento.
    """
    if _scalar(conn, "SELECT count(*) FROM embedding_spaces") == 1:
        row = conn.execute(
            # Tres búsquedas en el índice (workspace_id, embedding_model): un único
            # `embedding_model IS NOT ?` lo recorrería entero.
            "SELECT EXISTS (SELECT 1 FROM memories WHERE workspace_id = ?1 "
            "AND embedding_model IS NULL) "
            "OR EXISTS (SELECT 1 FROM memories WHERE workspace_id = ?1 AND embedding_model < ?2) "
            "OR EXISTS (SELECT 1 FROM memories WHERE workspace_id = ?1 AND embedding_model > ?2)",
            (workspace_id, space.model),
        ).fetchone()
        return bool(row[0])
    row = conn.execute(
        f"SELECT 1 FROM memories AS m WHERE m.workspace_id = ? "
        f"AND NOT EXISTS (SELECT 1 FROM {space.exact} AS x WHERE x.seq = m.seq) LIMIT 1",
        (workspace_id,),
    ).fetchone()
    return row is not None


def has_vector(conn: sqlite3.Connection, space: EmbeddingSpace, seq: int) -> bool:
    row = conn.execute(f"SELECT 1 FROM {space.exact} WHERE seq = ?", (seq,)).fetchone()
    return row is not None


# --- integridad

_BROKEN_CHAINS_SQL = """
SELECT m.id FROM memories AS m
LEFT JOIN memories AS nxt ON nxt.id = m.superseded_by_id
LEFT JOIN memories AS prv ON prv.id = m.supersedes_id
WHERE (m.superseded_by_id IS NULL AND (m.status != 'active' OR m.valid_to != ?))
   OR (m.superseded_by_id IS NOT NULL AND (
          nxt.id IS NULL
          OR nxt.supersedes_id IS NULL
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
        problems.append(tr("{n} referencias rotas entre memorias", n=len(broken_refs)))
    try:
        conn.execute("INSERT INTO memories_fts (memories_fts) VALUES ('integrity-check')")
    except sqlite3.DatabaseError as exc:
        problems.append(tr("índice de texto dañado: {error}", error=exc))
    orphans = _scalar(
        conn, "SELECT count(*) FROM memories_fts WHERE rowid NOT IN (SELECT seq FROM memories)"
    )
    if orphans:
        problems.append(tr("{n} entradas del índice de texto sin memoria", n=orphans))
    missing = _scalar(
        conn, "SELECT count(*) FROM memories WHERE seq NOT IN (SELECT rowid FROM memories_fts)"
    )
    if missing:
        problems.append(tr("{n} memorias sin entrada en el índice de texto", n=missing))
    for (memory_id,) in conn.execute(_BROKEN_CHAINS_SQL, (OPEN_END_MS,)):
        problems.append(tr("cadena de reemplazos inconsistente en {id}", id=memory_id))
    for space in embedding_spaces(conn):
        orphan_vectors = _scalar(
            conn,
            f"SELECT count(*) FROM {space.table} WHERE rowid NOT IN (SELECT seq FROM memories)",
        )
        if orphan_vectors:
            problems.append(
                tr("{n} vectores de {model} sin memoria", n=orphan_vectors, model=space.model)
            )
        unpaired = _scalar(
            conn,
            f"SELECT (SELECT count(*) FROM {space.table} WHERE rowid NOT IN "
            f"(SELECT seq FROM {space.exact})) + (SELECT count(*) FROM {space.exact} "
            f"WHERE seq NOT IN (SELECT rowid FROM {space.table}))",
        )
        if unpaired:
            problems.append(
                tr(
                    "{n} vectores de {model} sin su pareja exacta o binaria",
                    n=unpaired,
                    model=space.model,
                )
            )
        stale = _scalar(
            conn,
            f"""
            SELECT count(*) FROM {space.table} AS v JOIN memories AS m ON m.seq = v.rowid
            WHERE v.workspace_id != m.workspace_id OR v.kind != m.kind
               OR v.valid_from != m.valid_from OR v.valid_to != m.valid_to
            """,
        )
        if stale:
            problems.append(
                tr(
                    "{n} vectores de {model} con metadatos desactualizados",
                    n=stale,
                    model=space.model,
                )
            )
    # `memories.embedding_model` es el modelo del último vector calculado. Si dice que hay
    # vector de un modelo, tiene que existir en alguno de los espacios de ese modelo (puede
    # haber varios, si el modelo cambió de dimensión).
    by_model: dict[str, list[EmbeddingSpace]] = {}
    for space in embedding_spaces(conn):
        by_model.setdefault(space.model, []).append(space)
    for model, spaces in by_model.items():
        missing_in_all = " ".join(
            f"AND seq NOT IN (SELECT seq FROM {space.exact})" for space in spaces
        )
        (claimed,) = conn.execute(
            f"SELECT count(*) FROM memories WHERE embedding_model = ? {missing_in_all}", (model,)
        ).fetchone()
        if claimed:
            problems.append(
                tr(
                    "{n} memorias dicen tener vector de {model} y no lo tienen",
                    n=claimed,
                    model=model,
                )
            )
    return problems
