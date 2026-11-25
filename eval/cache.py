"""Caché de embeddings en disco para las evaluaciones.

Envuelve un `Embedder` real. Guarda cada vector por (modelo, uso, texto), así repetir una
evaluación o reanudarla tras un corte no vuelve a calcular nada. Vive en eval/datasets/
(ignorado por git), en un archivo SQLite.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from array import array
from collections.abc import Sequence
from pathlib import Path

from lastword.embeddings import Embedder, ModelProfile, Purpose

CACHE_DIR = Path(__file__).parent / "datasets" / "cache"


class CachedEmbedder:
    def __init__(self, inner: Embedder, path: Path | None = None) -> None:
        self._inner = inner
        path = path or CACHE_DIR / f"{inner.model.replace(':', '_').replace('/', '_')}.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute("CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, data BLOB)")
        self._db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        self._check_fingerprint(path)

    def _check_fingerprint(self, path: Path) -> None:
        """Los vectores dependen del formato de entrada (perfil del modelo). Si cambió, la caché
        ya no sirve: mejor fallar que devolver vectores de otro formato sin avisar."""
        profile = self._inner.profile
        current = hashlib.sha256(
            "\0".join([self.model, profile.document, profile.query, profile.similarity]).encode()
        ).hexdigest()
        row = self._db.execute("SELECT value FROM meta WHERE key = 'fingerprint'").fetchone()
        if row is None:
            self._db.execute("INSERT INTO meta VALUES ('fingerprint', ?)", (current,))
            self._db.commit()
        elif row[0] != current:
            raise RuntimeError(
                f"La caché {path} se creó con otro formato de entrada para {self.model}. "
                "Bórrala para recalcular los embeddings."
            )

    @property
    def model(self) -> str:
        return self._inner.model

    @property
    def profile(self) -> ModelProfile:
        return self._inner.profile

    def _key(self, text: str, purpose: Purpose) -> str:
        raw = f"{self.model}\0{purpose}\0{text}".encode()
        return hashlib.sha256(raw).hexdigest()

    def _lookup(self, keys: Sequence[str]) -> dict[str, list[float]]:
        found: dict[str, list[float]] = {}
        for start in range(0, len(keys), 500):
            chunk = keys[start : start + 500]
            marks = ", ".join("?" for _ in chunk)
            for key, data in self._db.execute(
                f"SELECT key, data FROM vectors WHERE key IN ({marks})", chunk
            ):
                found[key] = array("f", data).tolist()
        return found

    def embed(self, texts: Sequence[str], purpose: Purpose) -> list[list[float]]:
        keys = [self._key(text, purpose) for text in texts]
        found = self._lookup(keys)
        pending: dict[str, str] = {}  # clave → texto, sin repetir y en orden
        for key, text in zip(keys, texts, strict=True):
            if key not in found:
                pending.setdefault(key, text)
        if pending:
            vectors = self._inner.embed(list(pending.values()), purpose)
            rows = [(key, array("f", v).tobytes()) for key, v in zip(pending, vectors, strict=True)]
            self._db.executemany("INSERT OR REPLACE INTO vectors VALUES (?, ?)", rows)
            self._db.commit()
            found.update(zip(pending, vectors, strict=True))
        return [found[key] for key in keys]

    def warm(self, texts: Sequence[str], purpose: Purpose, *, batch: int = 256) -> None:
        """Calcula por lotes lo que falte, mostrando el progreso."""
        unique = list(dict.fromkeys(texts))
        cached = self._lookup([self._key(text, purpose) for text in unique])
        pending = [text for text in unique if self._key(text, purpose) not in cached]
        total, start = len(pending), time.perf_counter()
        print(f"{len(unique)} textos ({purpose}); faltan {total}", flush=True)
        for done in range(0, total, batch):
            self.embed(pending[done : done + batch], purpose)
            finished = min(done + batch, total)
            rate = finished / (time.perf_counter() - start)
            minutes = (total - finished) / rate / 60
            print(f"  {finished}/{total} · {rate:.0f}/s · faltan {minutes:.0f} min", flush=True)

    def close(self) -> None:
        self._db.close()
