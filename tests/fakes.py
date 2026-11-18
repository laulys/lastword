"""Embedders de prueba: deterministas y sin red."""

from __future__ import annotations

import re
import zlib
from collections.abc import Mapping, Sequence

from vigente.embeddings import ModelProfile, Purpose
from vigente.errors import EmbeddingError
from vigente.retrieval import STOPWORDS

# Para FakeEmbedder, estas palabras significan lo mismo: «base de datos».
DATABASE_WORDS = {"postgresql": "bd", "sqlite": "bd", "base": "bd", "datos": "bd"}


class FakeEmbedder:
    """Bolsa de palabras con hashing.

    `synonyms` hace que palabras distintas compartan dimensión, para simular lo que un modelo
    real considera parecido (por ejemplo, «SQLite» y «PostgreSQL»).
    """

    def __init__(
        self,
        *,
        model: str = "fake-embedder",
        dim: int = 256,
        synonyms: Mapping[str, str] | None = None,
        candidate_threshold: float = 0.3,
        min_recall_similarity: float = 0.0,
    ) -> None:
        self.model = model
        self.dim = dim
        self.profile = ModelProfile(
            candidate_threshold=candidate_threshold,
            min_recall_similarity=min_recall_similarity,
        )
        self._synonyms = {word.lower(): group for word, group in (synonyms or {}).items()}
        self.calls: list[tuple[Purpose, list[str]]] = []

    def embed(self, texts: Sequence[str], purpose: Purpose) -> list[list[float]]:
        self.calls.append((purpose, list(texts)))
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        words = [w for w in re.findall(r"\w+", text.lower()) if w not in STOPWORDS] or ["<vacío>"]
        vector = [0.0] * self.dim
        for word in words:
            vector[zlib.crc32(self._synonyms.get(word, word).encode()) % self.dim] += 1.0
        return vector


class FixedEmbedder:
    """Devuelve siempre el mismo vector: sirve para buscar sus bytes en el disco."""

    def __init__(self, vector: Sequence[float]) -> None:
        self.model = "fixed-embedder"
        self.profile = ModelProfile()
        self._vector = list(vector)

    def embed(self, texts: Sequence[str], purpose: Purpose) -> list[list[float]]:
        return [list(self._vector) for _ in texts]


class FailingEmbedder:
    """Simula un servicio de embeddings caído."""

    model = "failing-embedder"
    profile = ModelProfile()

    def embed(self, texts: Sequence[str], purpose: Purpose) -> list[list[float]]:
        raise EmbeddingError("Servicio de embeddings caído (simulado).")
