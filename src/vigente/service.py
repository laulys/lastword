"""Casos de uso. Aquí viven las reglas de reemplazo y las transacciones.

«Vigente en el instante t» significa `valid_from <= t < valid_to`. `status` solo dice si la
memoria tiene sucesora; la búsqueda filtra por fechas, no por `status`.

Los embeddings se calculan siempre fuera de las transacciones: son llamadas de red y no deben
bloquear la base de datos. Si el servicio de embeddings falla, la memoria se guarda igual (sin
vector), la respuesta lo avisa y `reindex` lo completa después.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import TypeAdapter

from vigente import retrieval, store
from vigente.embeddings import Embedder, Purpose
from vigente.errors import (
    AlreadySupersededError,
    ConfigError,
    EmbeddingError,
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
    ReindexResult,
    RememberResult,
    SupersedeRequest,
    SupersedeResult,
    WorkspaceId,
)

Clock = Callable[[], datetime]

CANDIDATE_LIMIT = 3
MAX_CHAIN_LENGTH = 10_000
REINDEX_BATCH = 32
_ONE_MS = timedelta(milliseconds=1)

_workspace_id = TypeAdapter(WorkspaceId)
_memory_id = TypeAdapter(MemoryId)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _truncate(value: datetime) -> datetime:
    """Precisión de milisegundos, igual que en la base de datos."""
    return store.from_ms(store.to_ms(value))


def _candidate_pool(k: int) -> int:
    """Cuántos resultados pedir a cada búsqueda antes de fusionar."""
    return min(max(k * 4, 20), 200)


class MemoryService:
    """Operaciones sobre la memoria de un único workspace."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        *,
        clock: Clock = utc_now,
        embedder: Embedder | None = None,
    ) -> None:
        self._conn = conn
        self._clock = clock
        self._embedder = embedder
        self.workspace_id: str = _workspace_id.validate_python(workspace_id)
        with store.transaction(conn):
            store.ensure_workspace(conn, self.workspace_id, store.to_ms(self._now()))

    @classmethod
    def open(
        cls,
        db_path: str | Path,
        workspace_id: str,
        *,
        clock: Clock = utc_now,
        embedder: Embedder | None = None,
    ) -> MemoryService:
        conn = store.connect(db_path)
        try:
            return cls(conn, workspace_id, clock=clock, embedder=embedder)
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
        warnings: list[str] = []
        vector = self._embed(memory.content, "document", warnings)
        with store.transaction(self._conn):
            seq = store.insert_memory(self._conn, memory)
            if vector is not None:
                self._store_vector(seq, memory, vector, now)
        stored = self._require(memory.id)
        candidates = (
            self._possible_supersedes(stored, vector, warnings) if stored.kind is Kind.FACT else []
        )
        return RememberResult(memory=stored, possible_supersedes=candidates, warnings=warnings)

    def supersede(self, request: SupersedeRequest) -> SupersedeResult:
        """Reemplaza un hecho vigente por uno nuevo sin editar ni borrar el anterior."""
        now = self._now()
        # Validar antes de llamar al servicio de embeddings: una petición que va a fallar no
        # debe esperar a la red. Se vuelve a validar dentro de la transacción por si cambió.
        self._check_supersedable(self._require(request.old_id))
        warnings: list[str] = []
        vector = self._embed(request.new_content, "document", warnings)
        with store.transaction(self._conn):
            old = self._require(request.old_id)
            self._check_supersedable(old)
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
            seq = store.insert_memory(self._conn, new)
            if vector is not None:
                self._store_vector(seq, new, vector, now)
            store.mark_superseded(
                self._conn,
                self.workspace_id,
                old.id,
                valid_to=valid_from,
                superseded_by_id=new.id,
            )
            return SupersedeResult(
                new=self._require(new.id), old=self._require(old.id), warnings=warnings
            )

    def forget(self, memory_id: str) -> list[str]:
        """Borra para siempre el hecho y todas sus versiones. Devuelve los ids borrados."""
        with store.transaction(self._conn):
            ids = [memory.id for memory in self._chain(self._require(memory_id))]
            store.delete_memories(self._conn, self.workspace_id, ids)
        store.checkpoint(self._conn)
        return ids

    def reindex(self) -> ReindexResult:
        """Calcula los vectores que falten con el modelo configurado."""
        if self._embedder is None:
            raise ConfigError("No hay modelo de embeddings configurado.")
        model = self._embedder.model
        embedded = 0
        while True:
            space = store.find_embedding_space(self._conn, model)
            pending = store.memories_without_vector(
                self._conn, self.workspace_id, space, limit=REINDEX_BATCH
            )
            if not pending:
                break
            vectors = self._embedder.embed([memory.content for _, memory in pending], "document")
            added = 0
            with store.transaction(self._conn):
                # Todos los vectores de un lote salen del mismo modelo: mismo espacio.
                target = store.ensure_embedding_space(
                    self._conn, model, len(vectors[0]), store.to_ms(self._now())
                )
                for (seq, _), vector in zip(pending, vectors, strict=True):
                    # Releer dentro de la transacción: pudo cambiar o borrarse mientras tanto.
                    current = store.get_memory_by_seq(self._conn, self.workspace_id, seq)
                    if current is None or store.has_vector(self._conn, target, seq):
                        continue
                    store.insert_vector(self._conn, target, seq, current, vector)
                    added += 1
            embedded += added
            if added == 0:  # nada que hacer en este lote: evita un bucle infinito
                break
        space = store.find_embedding_space(self._conn, model)
        return ReindexResult(
            model=model, dim=None if space is None else space.dim, embedded=embedded
        )

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
        pool = _candidate_pool(query.k)
        text_hits = retrieval.search_fts(
            self._conn, self.workspace_id, query.query, k=pool, at_ms=at_ms, kind=query.kind
        )
        warnings: list[str] = []
        vector = self._embed(query.query, "query", warnings)
        if self._embedder is None or vector is None:
            return RecallResult(mode="fts_only", results=text_hits[: query.k], warnings=warnings)
        space = store.find_embedding_space(self._conn, self._embedder.model, len(vector))
        if space is None:
            # No hay vectores de este modelo y dimensión: la búsqueda es solo de texto, y se
            # dice, en vez de llamarla híbrida.
            if store.count_without_vector(self._conn, self.workspace_id, None):
                warnings.append(
                    f"No hay vectores de {self._embedder.model} ({len(vector)} dimensiones). "
                    "Ejecuta `vigente reindex`. Se usó solo la búsqueda de texto."
                )
            return RecallResult(mode="fts_only", results=text_hits[: query.k], warnings=warnings)
        vector_hits = retrieval.search_vector(
            self._conn,
            space,
            self.workspace_id,
            vector,
            k=pool,
            at_ms=at_ms,
            kind=query.kind,
            min_similarity=self._embedder.profile.min_recall_similarity,
        )
        fused = retrieval.reciprocal_rank_fusion([text_hits, vector_hits], limit=query.k)
        return RecallResult(mode="hybrid", results=fused, warnings=warnings)

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
        counts = store.stats(self._conn, self.workspace_id)
        if self._embedder is not None:
            space = store.find_embedding_space(self._conn, self._embedder.model)
            counts["without_vector"] = store.count_without_vector(
                self._conn, self.workspace_id, space
            )
        return counts

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

    def _check_supersedable(self, old: Memory) -> None:
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

    def _embed(self, text: str, purpose: Purpose, warnings: list[str]) -> list[float] | None:
        """Un vector, o None si no hay embedder o el servicio falla (con aviso)."""
        if self._embedder is None:
            return None
        try:
            return self._embedder.embed([text], purpose)[0]
        except EmbeddingError as exc:
            warnings.append(f"{exc.message} Se usó solo la búsqueda de texto.")
            return None

    def _store_vector(self, seq: int, memory: Memory, vector: list[float], now: datetime) -> None:
        assert self._embedder is not None
        space = store.ensure_embedding_space(
            self._conn, self._embedder.model, len(vector), store.to_ms(now)
        )
        store.insert_vector(self._conn, space, seq, memory, vector)

    def _possible_supersedes(
        self, memory: Memory, document_vector: list[float] | None, warnings: list[str]
    ) -> list[Hit]:
        """Hechos vigentes parecidos al nuevo. El agente decide si alguno queda reemplazado.

        Con embeddings: vecinos por encima del umbral calibrado para el modelo, que detectan
        paráfrasis sin palabras en común. Sin embeddings: BM25, más débil.
        """
        at_ms = store.to_ms(self._now())
        if self._embedder is not None and document_vector is not None:
            profile = self._embedder.profile
            probe: list[float] | None = document_vector
            if profile.candidate_purpose != "document":
                probe = self._embed(memory.content, profile.candidate_purpose, warnings)
            if probe is not None:
                space = store.find_embedding_space(self._conn, self._embedder.model, len(probe))
                if space is None:
                    return []
                hits = retrieval.search_vector(
                    self._conn,
                    space,
                    self.workspace_id,
                    probe,
                    k=CANDIDATE_LIMIT + 1,
                    at_ms=at_ms,
                    kind=Kind.FACT,
                    min_similarity=profile.candidate_threshold,
                )
                return [hit for hit in hits if hit.memory.id != memory.id][:CANDIDATE_LIMIT]
        return retrieval.search_fts(
            self._conn,
            self.workspace_id,
            memory.content,
            k=CANDIDATE_LIMIT,
            at_ms=at_ms,
            kind=Kind.FACT,
            exclude_id=memory.id,
        )
