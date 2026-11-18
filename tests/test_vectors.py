from __future__ import annotations

import sqlite3
import struct
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

import pytest

from tests.fakes import DATABASE_WORDS, FailingEmbedder, FakeEmbedder, FixedEmbedder
from tests.helpers import T0, FakeClock, disk_bytes, recall_ids, remember, supersede
from vigente import store
from vigente.embeddings import Purpose
from vigente.errors import ConfigError, NotSupersedableError
from vigente.models import (
    Hit,
    Kind,
    Memory,
    NewMemory,
    RecallQuery,
    SearchMode,
    SupersedeRequest,
)
from vigente.retrieval import hybrid, reciprocal_rank_fusion
from vigente.service import MemoryService


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder(synonyms=DATABASE_WORDS)


@pytest.fixture
def semantic(conn: sqlite3.Connection, clock: FakeClock, embedder: FakeEmbedder) -> MemoryService:
    return MemoryService(conn, "test", clock=clock, embedder=embedder)


class TestWrite:
    def test_stores_a_vector_in_the_space_of_its_model(
        self, semantic: MemoryService, conn: sqlite3.Connection
    ) -> None:
        memory = remember(semantic, "Ana vive en Madrid")
        assert memory.embedding_model == "fake-embedder"
        spaces = store.embedding_spaces(conn)
        assert [(space.model, space.dim) for space in spaces] == [("fake-embedder", 256)]
        assert semantic.stats()["without_vector"] == 0
        assert semantic.doctor() == []

    def test_documents_and_queries_use_their_own_format(
        self, semantic: MemoryService, embedder: FakeEmbedder
    ) -> None:
        remember(semantic, "Ana vive en Madrid")
        semantic.recall(RecallQuery(query="¿Dónde vive Ana?"))
        purposes = [purpose for purpose, _ in embedder.calls]
        assert purposes[0] == "document"
        assert purposes[-1] == "query"

    def test_each_model_and_dimension_gets_its_own_index(
        self, conn: sqlite3.Connection, clock: FakeClock
    ) -> None:
        first = MemoryService(conn, "test", clock=clock, embedder=FakeEmbedder(model="a", dim=32))
        remember(first, "Ana vive en Madrid")
        second = MemoryService(conn, "test", clock=clock, embedder=FakeEmbedder(model="b", dim=48))
        assert second.stats()["without_vector"] == 1  # los vectores de otro modelo no sirven
        second.reindex()
        spaces = store.embedding_spaces(conn)
        assert [(space.model, space.dim) for space in spaces] == [("a", 32), ("b", 48)]
        assert second.doctor() == []


class TestPossibleSupersedes:
    def test_embeddings_catch_paraphrases_that_text_search_misses(
        self, conn: sqlite3.Connection, clock: FakeClock, embedder: FakeEmbedder
    ) -> None:
        plain = MemoryService(conn, "plain", clock=clock)
        semantic = MemoryService(conn, "semantic", clock=clock, embedder=embedder)
        for service in (plain, semantic):
            remember(service, "El proyecto usa PostgreSQL")
            remember(service, "Ana vive en Madrid")
        clock.advance(days=1)

        assert plain.remember(NewMemory(content="Migramos a SQLite")).possible_supersedes == []

        result = semantic.remember(NewMemory(content="Migramos a SQLite"))
        assert [hit.memory.content for hit in result.possible_supersedes] == [
            "El proyecto usa PostgreSQL"
        ]
        hit = result.possible_supersedes[0]
        assert hit.matched_by == ["vector"]
        assert hit.similarity is not None
        assert hit.similarity >= embedder.profile.candidate_threshold

    def test_the_threshold_filters_weak_matches(
        self, conn: sqlite3.Connection, clock: FakeClock
    ) -> None:
        strict = FakeEmbedder(synonyms=DATABASE_WORDS, candidate_threshold=0.99)
        service = MemoryService(conn, "test", clock=clock, embedder=strict)
        remember(service, "El proyecto usa PostgreSQL")
        clock.advance(days=1)
        assert service.remember(NewMemory(content="Migramos a SQLite")).possible_supersedes == []

    def test_only_current_facts_are_suggested(
        self, semantic: MemoryService, clock: FakeClock
    ) -> None:
        postgres = remember(semantic, "El proyecto usa PostgreSQL")
        clock.advance(days=1)
        sqlite = supersede(semantic, postgres.id, "El proyecto usa SQLite")
        remember(semantic, "Migramos el servidor de base de datos", kind="episode")
        clock.advance(days=1)
        result = semantic.remember(NewMemory(content="Cambiamos la base de datos otra vez"))
        assert [hit.memory.id for hit in result.possible_supersedes] == [sqlite.id]


class TestHybridRecall:
    def test_combines_text_and_vector_results(
        self, conn: sqlite3.Connection, clock: FakeClock, embedder: FakeEmbedder
    ) -> None:
        semantic = MemoryService(
            conn, "test", clock=clock, embedder=embedder, search_mode=SearchMode.HYBRID
        )
        remember(semantic, "Desplegamos en Fly.io")
        both = remember(semantic, "El proyecto usa SQLite")
        only_vector = remember(semantic, "Guardamos todo en PostgreSQL")
        result = semantic.recall(RecallQuery(query="¿Qué base de datos usa el proyecto?"))
        assert result.mode == "hybrid"
        top = result.results[:2]
        assert [(hit.memory.id, hit.matched_by) for hit in top] == [
            (both.id, ["fts", "vector"]),
            (only_vector.id, ["vector"]),
        ]
        assert top[0].similarity is not None

    def test_min_similarity_drops_unrelated_neighbors(
        self, conn: sqlite3.Connection, clock: FakeClock
    ) -> None:
        picky = FakeEmbedder(synonyms=DATABASE_WORDS, min_recall_similarity=0.2)
        service = MemoryService(conn, "test", clock=clock, embedder=picky)
        unrelated = remember(service, "Desplegamos en Fly.io")
        related = remember(service, "Guardamos todo en PostgreSQL")
        ids = recall_ids(service, "¿Qué base de datos usamos?")
        assert related.id in ids
        assert unrelated.id not in ids

    def test_vector_search_respects_validity(
        self, semantic: MemoryService, clock: FakeClock
    ) -> None:
        postgres = remember(semantic, "El proyecto usa PostgreSQL")
        clock.advance(days=60)
        sqlite = supersede(semantic, postgres.id, "El proyecto usa SQLite")
        clock.advance(days=10)
        query = "¿Qué base de datos tenemos?"
        assert recall_ids(semantic, query) == [sqlite.id]
        assert recall_ids(semantic, query, as_of=T0 + timedelta(days=30)) == [postgres.id]

    def test_vector_search_is_isolated_by_workspace(
        self, conn: sqlite3.Connection, clock: FakeClock, embedder: FakeEmbedder
    ) -> None:
        ana = MemoryService(conn, "ana", clock=clock, embedder=embedder)
        bruno = MemoryService(conn, "bruno", clock=clock, embedder=embedder)
        remember(ana, "El proyecto usa PostgreSQL")
        assert recall_ids(bruno, "base de datos", include_history=True) == []

    def test_kind_filter_applies_to_vectors(self, semantic: MemoryService) -> None:
        fact = remember(semantic, "El proyecto usa PostgreSQL")
        remember(semantic, "Instalamos SQLite en el portátil", kind="episode")
        assert recall_ids(semantic, "base de datos", kind=Kind.FACT) == [fact.id]


class TestDegradation:
    def test_memories_are_kept_when_the_embedding_service_fails(
        self, conn: sqlite3.Connection, clock: FakeClock
    ) -> None:
        offline = MemoryService(conn, "test", clock=clock, embedder=FailingEmbedder())
        result = offline.remember(NewMemory(content="Ana vive en Madrid"))
        assert result.memory.embedding_model is None
        assert result.warnings
        assert offline.get(result.memory.id) == result.memory
        recall = offline.recall(RecallQuery(query="Ana Madrid"))
        assert recall.mode == "fts_only"
        assert recall.warnings
        assert [hit.memory.id for hit in recall.results] == [result.memory.id]

    def test_reindex_fills_missing_vectors_with_current_validity(
        self, conn: sqlite3.Connection, clock: FakeClock, embedder: FakeEmbedder
    ) -> None:
        offline = MemoryService(conn, "test", clock=clock, embedder=FailingEmbedder())
        postgres = remember(offline, "El proyecto usa PostgreSQL")
        clock.advance(days=1)
        supersede(offline, postgres.id, "El proyecto usa SQLite")
        remember(offline, "Ana vive en Madrid")

        online = MemoryService(conn, "test", clock=clock, embedder=embedder)
        assert online.stats()["without_vector"] == 3
        result = online.reindex()
        assert (result.embedded, result.dim) == (3, 256)
        assert online.stats()["without_vector"] == 0
        assert online.doctor() == []
        assert recall_ids(online, "base de datos", as_of=T0) == [postgres.id]
        assert online.reindex().embedded == 0

    def test_reindex_requires_a_model(self, service: MemoryService) -> None:
        with pytest.raises(ConfigError):
            service.reindex()


class TestForgetAndIntegrity:
    def test_forget_erases_vectors_from_disk(
        self, conn: sqlite3.Connection, clock: FakeClock, db_path: Path
    ) -> None:
        vector = [1.2345678, -9.8765432, 3.1415926, 2.7182818, -1.4142135, 0.5772156]
        service = MemoryService(conn, "test", clock=clock, embedder=FixedEmbedder(vector))
        memory = remember(service, "Ana vive en Madrid")
        raw = struct.pack(f"{len(vector)}f", *vector)
        assert raw in disk_bytes(db_path)  # control: el vector estaba en disco
        service.forget(memory.id)
        assert raw not in disk_bytes(db_path)
        assert service.doctor() == []

    def test_doctor_detects_stale_vector_metadata(
        self, semantic: MemoryService, conn: sqlite3.Connection, clock: FakeClock
    ) -> None:
        postgres = remember(semantic, "El proyecto usa PostgreSQL")
        clock.advance(days=1)
        supersede(semantic, postgres.id, "El proyecto usa SQLite")
        (space,) = store.embedding_spaces(conn)
        conn.execute(
            f"UPDATE {space.table} SET valid_to = ? "
            "WHERE rowid = (SELECT seq FROM memories WHERE id = ?)",
            (store.OPEN_END_MS, postgres.id),
        )
        assert any("desactualizados" in problem for problem in semantic.doctor())

    def test_doctor_detects_orphan_vectors(
        self, semantic: MemoryService, conn: sqlite3.Connection
    ) -> None:
        memory = remember(semantic, "Ana vive en Madrid")
        (space,) = store.embedding_spaces(conn)
        seq = conn.execute("SELECT seq FROM memories WHERE id = ?", (memory.id,)).fetchone()[0]
        conn.execute("PRAGMA foreign_keys = OFF")  # simular una base dañada
        conn.execute("DELETE FROM memories_fts WHERE rowid = ?", (seq,))
        conn.execute("DELETE FROM memories WHERE seq = ?", (seq,))
        conn.execute("PRAGMA foreign_keys = ON")
        assert any("sin memoria" in problem for problem in semantic.doctor())
        assert store.has_vector(conn, space, seq)


def _hit(memory_id: str, source: str) -> Hit:
    memory = Memory(
        id=memory_id, workspace_id="test", kind=Kind.FACT, content=memory_id, tags=[],
        created_at=T0, valid_from=T0,
    )  # fmt: skip
    return Hit(memory=memory, score=0.0, matched_by=[source])


class TestReciprocalRankFusion:
    def test_rewards_documents_found_by_both_searches(self) -> None:
        text = [_hit("A", "fts"), _hit("B", "fts"), _hit("C", "fts")]
        vector = [_hit("C", "vector"), _hit("A", "vector")]
        fused = reciprocal_rank_fusion([text, vector], limit=3)
        assert [hit.memory.id for hit in fused] == ["A", "C", "B"]
        assert fused[0].matched_by == ["fts", "vector"]
        assert fused[2].matched_by == ["fts"]
        assert fused[0].score == pytest.approx(1 / 61 + 1 / 62)

    def test_ties_prefer_the_earlier_ranking_not_the_id(self) -> None:
        # Los ULID tienen una parte aleatoria: desempatar por id cambiaría entre ejecuciones.
        first, second = [_hit("Z", "vector")], [_hit("A", "fts")]
        assert [h.memory.id for h in reciprocal_rank_fusion([first, second], limit=2)] == ["Z", "A"]
        assert [h.memory.id for h in reciprocal_rank_fusion([second, first], limit=2)] == ["A", "Z"]

    def test_hybrid_gives_vectors_priority_in_ties(self) -> None:
        fused = hybrid([_hit("A", "fts")], [_hit("Z", "vector")], limit=1)
        assert [hit.memory.id for hit in fused] == ["Z"]


class TestReviewFixes:
    def test_supersede_validates_before_calling_the_embedding_service(
        self, semantic: MemoryService, embedder: FakeEmbedder
    ) -> None:
        episode = remember(semantic, "Reunión de arranque", kind="episode")
        calls = len(embedder.calls)
        with pytest.raises(NotSupersedableError):
            supersede(semantic, episode.id, "Otra reunión")
        assert len(embedder.calls) == calls

    def test_recall_without_vectors_for_the_model_says_so(
        self, conn: sqlite3.Connection, clock: FakeClock, embedder: FakeEmbedder
    ) -> None:
        remember(MemoryService(conn, "test", clock=clock), "Ana vive en Madrid")
        result = MemoryService(conn, "test", clock=clock, embedder=embedder).recall(
            RecallQuery(query="Ana")
        )
        assert result.mode == "fts_only"
        assert any("reindex" in warning for warning in result.warnings)

    def test_doctor_detects_a_memory_that_claims_a_missing_vector(
        self, semantic: MemoryService, conn: sqlite3.Connection
    ) -> None:
        memory = remember(semantic, "Ana vive en Madrid")
        (space,) = store.embedding_spaces(conn)
        seq = conn.execute("SELECT seq FROM memories WHERE id = ?", (memory.id,)).fetchone()[0]
        conn.execute(f"DELETE FROM {space.table} WHERE rowid = ?", (seq,))
        conn.execute(f"DELETE FROM {space.exact} WHERE seq = ?", (seq,))
        assert any("dicen tener vector" in problem for problem in semantic.doctor())

    def test_doctor_detects_a_vector_without_its_pair(
        self, semantic: MemoryService, conn: sqlite3.Connection
    ) -> None:
        memory = remember(semantic, "Ana vive en Madrid")
        (space,) = store.embedding_spaces(conn)
        seq = conn.execute("SELECT seq FROM memories WHERE id = ?", (memory.id,)).fetchone()[0]
        conn.execute(f"DELETE FROM {space.table} WHERE rowid = ?", (seq,))  # falta el binario
        assert any("sin su pareja" in problem for problem in semantic.doctor())


def test_linking_updates_the_vector_index(semantic: MemoryService, clock: FakeClock) -> None:
    postgres = remember(semantic, "El proyecto usa PostgreSQL")
    clock.advance(days=1)
    sqlite = remember(semantic, "El proyecto usa SQLite")
    semantic.supersede(SupersedeRequest(old_id=postgres.id, new_id=sqlite.id))
    assert recall_ids(semantic, "base de datos") == [sqlite.id]
    assert semantic.doctor() == []


def test_weights_shift_the_fusion_towards_one_ranking() -> None:
    text = [_hit("A", "fts"), _hit("B", "fts")]
    vector = [_hit("B", "vector"), _hit("A", "vector")]
    assert [h.memory.id for h in reciprocal_rank_fusion([text, vector], limit=2)] == ["A", "B"]
    weighted = reciprocal_rank_fusion([text, vector], limit=2, weights=[1.0, 2.0])
    assert [h.memory.id for h in weighted] == ["B", "A"]
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([text, vector], limit=2, weights=[1.0])


class TestSearchModes:
    """Por defecto se busca por significado (eval/results/decisions.md)."""

    def test_default_mode_is_vector(self, semantic: MemoryService) -> None:
        memory = remember(semantic, "Guardamos todo en PostgreSQL")
        result = semantic.recall(RecallQuery(query="¿Qué base de datos usamos?"))
        assert result.mode == "vector"
        assert [(hit.memory.id, hit.matched_by) for hit in result.results][:1] == [
            (memory.id, ["vector"])
        ]

    def test_text_mode_never_embeds_the_query(
        self, conn: sqlite3.Connection, clock: FakeClock, embedder: FakeEmbedder
    ) -> None:
        service = MemoryService(
            conn, "test", clock=clock, embedder=embedder, search_mode=SearchMode.TEXT
        )
        memory = remember(service, "Ana vive en Madrid")
        result = service.recall(RecallQuery(query="Ana Madrid"))
        assert (result.mode, [hit.memory.id for hit in result.results]) == ("fts_only", [memory.id])
        assert "query" not in [purpose for purpose, _ in embedder.calls]


def test_memories_without_vectors_are_still_found(
    conn: sqlite3.Connection, clock: FakeClock, embedder: FakeEmbedder
) -> None:
    offline = remember(
        MemoryService(conn, "test", clock=clock, embedder=FailingEmbedder()), "Ana vive en Madrid"
    )
    online = MemoryService(conn, "test", clock=clock, embedder=embedder)
    remember(online, "El proyecto usa SQLite")
    result = online.recall(RecallQuery(query="Ana Madrid"))
    assert result.mode == "hybrid"
    assert offline.id in [hit.memory.id for hit in result.results]
    assert any("reindex" in warning for warning in result.warnings)
    online.reindex()
    result = online.recall(RecallQuery(query="Ana Madrid"))
    assert (result.mode, result.warnings) == ("vector", [])


def test_a_stored_null_vector_does_not_break_search(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """Un vector de ceros no tiene distancia coseno. El cliente HTTP ya los rechaza, pero uno
    guardado por otro proveedor no debe dejar el workspace sin búsqueda."""
    broken = MemoryService(conn, "test", clock=clock, embedder=FixedEmbedder([0.0, 0.0, 0.0, 0.0]))
    remember(broken, "Ana vive en Madrid")
    healthy = MemoryService(conn, "test", clock=clock, embedder=FixedEmbedder([1.0, 0.0, 0.0, 0.0]))
    luis = remember(healthy, "Luis vive en Valencia")
    assert recall_ids(healthy, "dónde vive Luis") == [luis.id]
    assert healthy.doctor() == []


class TestSeveralModels:
    """Lo que pasa cuando no todas las memorias tienen vector del modelo en uso."""

    def test_a_fact_saved_without_vector_is_still_proposed(
        self, conn: sqlite3.Connection, clock: FakeClock, embedder: FakeEmbedder
    ) -> None:
        """Guardado con el servicio caído, solo lo encuentra el texto. Sin proponerlo, la
        versión vieja y la nueva quedarían vigentes."""
        old = remember(MemoryService(conn, "test", clock=clock), "El proyecto usa PostgreSQL 15")
        semantic = MemoryService(conn, "test", clock=clock, embedder=embedder)
        result = semantic.remember(NewMemory(content="El proyecto usa PostgreSQL 16"))
        assert [hit.memory.id for hit in result.possible_supersedes] == [old.id]
        assert any("vigente reindex" in warning for warning in result.warnings)

    def test_going_back_to_a_previous_model_keeps_vector_search(
        self, conn: sqlite3.Connection, clock: FakeClock
    ) -> None:
        first = MemoryService(conn, "test", clock=clock, embedder=FakeEmbedder(model="a"))
        ana = remember(first, "Ana vive en Madrid")
        assert (
            MemoryService(conn, "test", clock=clock, embedder=FakeEmbedder(model="b"))
            .reindex()
            .embedded
            == 1
        )
        result = first.recall(RecallQuery(query="Ana Madrid"))
        assert (result.mode, result.warnings) == ("vector", [])
        assert [hit.memory.id for hit in result.results] == [ana.id]

    def test_a_model_that_changes_dimension_warns_and_reindex_fixes_it(
        self, conn: sqlite3.Connection, clock: FakeClock
    ) -> None:
        small = MemoryService(conn, "test", clock=clock, embedder=FakeEmbedder(dim=64))
        ana = remember(small, "Ana vive en Madrid")
        large = MemoryService(conn, "test", clock=clock, embedder=FakeEmbedder(dim=128))
        remember(large, "Luis trabaja en Valencia")
        degraded = large.recall(RecallQuery(query="Ana Madrid"))
        assert degraded.mode == "hybrid"  # Ana no tiene vector de 128 dimensiones: la da el texto
        assert ana.id in [hit.memory.id for hit in degraded.results]
        assert large.doctor() == []

        assert large.reindex().embedded == 1
        assert large.recall(RecallQuery(query="Ana Madrid")).mode == "vector"
        # De vuelta en la dimensión anterior, a Luis le falta ese vector: reindex lo calcula.
        assert small.reindex().model_dump() == {"model": "fake-embedder", "dim": 64, "embedded": 1}
        assert small.recall(RecallQuery(query="Luis Valencia")).mode == "vector"

    def test_reindex_never_gives_a_forgotten_vector_to_another_memory(
        self, db_path: Path, clock: FakeClock
    ) -> None:
        """Mientras reindex espera al modelo, otro cliente olvida la memoria más reciente y
        guarda otra, que hereda su número de fila."""
        other = MemoryService.open(db_path, "test", clock=clock)
        forgotten = remember(other, "Ana tiene diabetes tipo uno")

        class DuringEmbedding(FakeEmbedder):
            pending: bool = True

            def embed(self, texts: Sequence[str], purpose: Purpose) -> list[list[float]]:
                if self.pending:
                    self.pending = False
                    other.forget(forgotten.id)
                    remember(other, "El gato se llama Michi")
                return super().embed(texts, purpose)

        reindexing = MemoryService.open(db_path, "test", clock=clock, embedder=DuringEmbedding())
        assert reindexing.reindex().embedded == 1  # el gato, con su propio vector
        (hit,) = reindexing.recall(RecallQuery(query="El gato se llama Michi")).results
        assert hit.similarity == pytest.approx(1.0)
        leaked = reindexing.recall(RecallQuery(query="Ana tiene diabetes tipo uno")).results
        assert all((hit.similarity or 0.0) < 0.5 for hit in leaked)
        other.close()
        reindexing.close()
