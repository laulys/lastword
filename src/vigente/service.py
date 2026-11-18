"""Casos de uso. Aquí viven las reglas de reemplazo y las transacciones.

«Vigente en el instante t» significa `valid_from <= t < valid_to`. `status` solo dice si la
memoria tiene sucesora; la búsqueda filtra por fechas, no por `status`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import TypeAdapter

from vigente import retrieval, store
from vigente.errors import (
    AlreadySupersededError,
    InvalidValidityError,
    NotFoundError,
    NotSupersedableError,
)
from vigente.ids import new_ulid
from vigente.models import (
    Hit,
    Kind,
    ListQuery,
    Memory,
    MemoryId,
    NewMemory,
    Page,
    RecallQuery,
    RecallResult,
    RememberResult,
    SupersedeRequest,
    SupersedeResult,
    WorkspaceId,
)

Clock = Callable[[], datetime]

CANDIDATE_LIMIT = 3
MAX_CHAIN_LENGTH = 10_000
_ONE_MS = timedelta(milliseconds=1)

_workspace_id = TypeAdapter(WorkspaceId)
_memory_id = TypeAdapter(MemoryId)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _truncate(value: datetime) -> datetime:
    """Precisión de milisegundos, igual que en la base de datos."""
    return store.from_ms(store.to_ms(value))


class MemoryService:
    """Operaciones sobre la memoria de un único workspace."""

    def __init__(
        self, conn: sqlite3.Connection, workspace_id: str, *, clock: Clock = utc_now
    ) -> None:
        self._conn = conn
        self._clock = clock
        self.workspace_id: str = _workspace_id.validate_python(workspace_id)
        with store.transaction(conn):
            store.ensure_workspace(conn, self.workspace_id, store.to_ms(self._now()))

    @classmethod
    def open(
        cls, db_path: str | Path, workspace_id: str, *, clock: Clock = utc_now
    ) -> MemoryService:
        conn = store.connect(db_path)
        try:
            return cls(conn, workspace_id, clock=clock)
        except BaseException:
            conn.close()
            raise

    def close(self) -> None:
        self._conn.close()

    # --- escritura

    def remember(self, request: NewMemory) -> RememberResult:
        now = self._now()
        valid_from = now if request.valid_from is None else _truncate(request.valid_from)
        if request.kind is Kind.EPISODE and valid_from > now:
            raise InvalidValidityError(
                "Un episode registra algo que ya pasó: valid_from no puede estar en el futuro."
            )
        memory = Memory(
            id=new_ulid(store.to_ms(now)),
            workspace_id=self.workspace_id,
            kind=request.kind,
            content=request.content,
            tags=list(request.tags),
            source=request.source,
            confidence=request.confidence,
            created_at=now,
            valid_from=valid_from,
        )
        with store.transaction(self._conn):
            store.insert_memory(self._conn, memory)
        candidates = self._possible_supersedes(memory) if memory.kind is Kind.FACT else []
        return RememberResult(memory=memory, possible_supersedes=candidates)

    def supersede(self, request: SupersedeRequest) -> SupersedeResult:
        """Reemplaza un hecho vigente por uno nuevo sin editar ni borrar el anterior."""
        now = self._now()
        with store.transaction(self._conn):
            old = self._require(request.old_id)
            if old.kind is not Kind.FACT:
                raise NotSupersedableError(
                    "Solo los fact se pueden reemplazar; un episode registra algo que ya pasó."
                )
            if old.superseded_by_id is not None:
                current = self._chain(old)[-1]
                raise AlreadySupersededError(
                    f"{old.id} ya fue reemplazada; la versión vigente es {current.id}.",
                    current_id=current.id,
                )
            if request.valid_from is None:
                # Sin fecha explícita: ahora, pero siempre después de que empezara la anterior.
                valid_from = max(now, old.valid_from + _ONE_MS)
            else:
                valid_from = _truncate(request.valid_from)
                if valid_from <= old.valid_from:
                    raise InvalidValidityError(
                        "La versión nueva debe empezar después de que empezara la anterior "
                        f"({old.valid_from.isoformat()})."
                    )
            new = Memory(
                id=new_ulid(store.to_ms(now)),
                workspace_id=self.workspace_id,
                kind=Kind.FACT,
                content=request.new_content,
                tags=list(old.tags if request.tags is None else request.tags),
                source=request.source,
                confidence=request.confidence,
                created_at=now,
                valid_from=valid_from,
                supersedes_id=old.id,
                supersede_reason=request.reason,
            )
            store.insert_memory(self._conn, new)
            store.mark_superseded(
                self._conn,
                self.workspace_id,
                old.id,
                valid_to=valid_from,
                superseded_by_id=new.id,
            )
            return SupersedeResult(new=self._require(new.id), old=self._require(old.id))

    def forget(self, memory_id: str) -> list[str]:
        """Borra para siempre el hecho y todas sus versiones. Devuelve los ids borrados."""
        with store.transaction(self._conn):
            ids = [memory.id for memory in self._chain(self._require(memory_id))]
            store.delete_memories(self._conn, self.workspace_id, ids)
        store.checkpoint(self._conn)
        return ids

    # --- lectura

    def get(self, memory_id: str) -> Memory:
        return self._require(memory_id)

    def history(self, memory_id: str) -> list[Memory]:
        """Todas las versiones del hecho, de la más antigua a la vigente."""
        with store.transaction(self._conn, write=False):
            return self._chain(self._require(memory_id))

    def recall(self, query: RecallQuery) -> RecallResult:
        if query.include_history:
            at_ms = None
        else:
            at_ms = store.to_ms(query.as_of if query.as_of is not None else self._now())
        hits = retrieval.search_fts(
            self._conn, self.workspace_id, query.query, k=query.k, at_ms=at_ms, kind=query.kind
        )
        return RecallResult(mode="fts_only", results=hits)

    def list_memories(self, query: ListQuery) -> Page:
        items, next_seq = store.list_memories(
            self._conn,
            self.workspace_id,
            kind=query.kind,
            status=query.status,
            tag=query.tag,
            limit=query.limit,
            before_seq=None if query.cursor is None else int(query.cursor),
        )
        return Page(items=items, next_cursor=None if next_seq is None else str(next_seq))

    def stats(self) -> dict[str, int]:
        return store.stats(self._conn, self.workspace_id)

    def doctor(self) -> list[str]:
        return store.integrity_problems(self._conn)

    # --- internos

    def _now(self) -> datetime:
        return _truncate(self._clock())

    def _require(self, memory_id: str) -> Memory:
        valid_id = _memory_id.validate_python(memory_id)
        memory = store.get_memory(self._conn, self.workspace_id, valid_id)
        if memory is None:
            raise NotFoundError(f"No existe la memoria {valid_id} en este workspace.")
        return memory

    def _chain(self, memory: Memory) -> list[Memory]:
        root = memory
        for _ in range(MAX_CHAIN_LENGTH):
            if root.supersedes_id is None:
                break
            root = self._require(root.supersedes_id)
        else:
            raise RuntimeError("Cadena de reemplazos demasiado larga o con un ciclo.")
        chain = [root]
        while chain[-1].superseded_by_id is not None:
            if len(chain) >= MAX_CHAIN_LENGTH:
                raise RuntimeError("Cadena de reemplazos demasiado larga o con un ciclo.")
            chain.append(self._require(chain[-1].superseded_by_id))
        return chain

    def _possible_supersedes(self, memory: Memory) -> list[Hit]:
        """Hechos vigentes parecidos al nuevo. El agente decide si alguno queda reemplazado."""
        return retrieval.search_fts(
            self._conn,
            self.workspace_id,
            memory.content,
            k=CANDIDATE_LIMIT,
            at_ms=store.to_ms(self._now()),
            kind=Kind.FACT,
            exclude_id=memory.id,
        )
