from __future__ import annotations

import sqlite3
import struct
from datetime import timedelta
from pathlib import Path

import pytest

from tests.fakes import FailingEmbedder, FakeEmbedder, FixedEmbedder
from tests.helpers import T0, FakeClock, disk_bytes, recall_ids, remember, supersede
from vigente import store
from vigente.errors import ConfigError, NotSupersedableError
from vigente.models import Hit, Kind, Memory, NewMemory, RecallQuery
from vigente.retrieval import reciprocal_rank_fusion
from vigente.service import MemoryService

# Para el embedder falso, estas palabras significan lo mismo: «base de datos».
DATABASE_WORDS = {"postgresql": "bd", "sqlite": "bd", "base": "bd", "datos": "bd"}


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
    def test_combines_text_and_vector_results(self, semantic: MemoryService) -> None:
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
        conn.execute("DELETE FROM memories_fts WHERE rowid = ?", (seq,))
        conn.execute("DELETE FROM memories WHERE seq = ?", (seq,))
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

    def test_respects_the_limit_and_breaks_ties_deterministically(self) -> None:
        fused = reciprocal_rank_fusion([[_hit("Z", "fts")], [_hit("Y", "vector")]], limit=1)
        assert [hit.memory.id for hit in fused] == ["Y"]


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
        conn.execute(
            f"DELETE FROM {space.table} WHERE rowid = (SELECT seq FROM memories WHERE id = ?)",
            (memory.id,),
        )
        assert any("dicen tener vector" in problem for problem in semantic.doctor())
