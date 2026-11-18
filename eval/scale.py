"""Latencia y escala: ¿hasta dónde llega un solo archivo SQLite con KNN exacto?

Usa vectores aleatorios de 768 dimensiones (las de embeddinggemma) para medir el coste de
Vigente sin mezclarlo con el del modelo, que se mide aparte (eval/embedding_choice.py y la
caché de E2). Las bases van a un directorio temporal, en disco, con WAL, como en uso real.

Uso:
    uv run python eval/scale.py [--sizes 1000 10000 50000]
"""

from __future__ import annotations

import argparse
import hashlib
import platform
import random
import tempfile
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from charts import bar_chart
from metrics import percentile

from vigente.embeddings import ModelProfile, Purpose
from vigente.models import Kind, ListQuery, NewMemory, RecallQuery
from vigente.service import MemoryService

HERE = Path(__file__).parent
RESULTS_MD = HERE / "results" / "scale.md"
CHART = HERE / "results" / "scale.svg"
DIM = 768
RECALLS, REMEMBERS, FORGETS = 50, 30, 10
WORDS = (  # noqa: SIM905 - más legible que 35 cadenas sueltas
    "proyecto base datos reunión equipo cliente viaje receta libro película música salud plan "
    "presupuesto fin semana ciudad amigo trabajo correo código lanzamiento diseño factura "
    "contrato oficina vacaciones horario deporte comida médico curso idioma coche casa"
).split()


class RandomEmbedder:
    """Vector pseudoaleatorio estable por texto: mide el almacenamiento, no el modelo."""

    model = "random-768"
    profile = ModelProfile(candidate_threshold=0.9)

    def embed(self, texts: Sequence[str], purpose: Purpose) -> list[list[float]]:
        vectors = []
        for text in texts:
            seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
            rng = random.Random(seed)
            vectors.append([rng.gauss(0.0, 1.0) for _ in range(DIM)])
        return vectors


def sentence(rng: random.Random, number: int) -> str:
    return f"Nota {number}: " + " ".join(rng.choices(WORDS, k=rng.randint(8, 30))) + "."


def timed(action: Callable[[int], object], times: int) -> list[float]:
    samples = []
    for i in range(times):
        start = time.perf_counter()
        action(i)
        samples.append((time.perf_counter() - start) * 1000)
    return samples


def measure(size: int, workdir: Path) -> dict[str, float]:
    rng = random.Random(size)
    service = MemoryService.open(workdir / f"scale-{size}.db", "escala", embedder=RandomEmbedder())
    start = time.perf_counter()
    for number in range(size):
        service.remember(NewMemory(content=sentence(rng, number), kind=Kind.EPISODE))
    load_ms = (time.perf_counter() - start) / size * 1000

    queries = [" ".join(rng.choices(WORDS, k=6)) for _ in range(RECALLS)]
    recall = timed(lambda i: service.recall(RecallQuery(query=queries[i])), RECALLS)
    remember = timed(
        lambda i: service.remember(NewMemory(content=sentence(rng, size + i))), REMEMBERS
    )
    victims = [m.id for m in service.list_memories(ListQuery(limit=FORGETS)).items]
    forget = timed(lambda i: service.forget(victims[i]), FORGETS)
    service.close()
    size_mb = sum(p.stat().st_size for p in workdir.glob(f"scale-{size}.db*")) / 1e6
    return {
        "load_ms": load_ms,
        "recall_p50": percentile(recall, 50),
        "recall_p95": percentile(recall, 95),
        "remember_p50": percentile(remember, 50),
        "remember_p95": percentile(remember, 95),
        "forget_p50": percentile(forget, 50),
        "forget_p95": percentile(forget, 95),
        "size_mb": size_mb,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=[1_000, 10_000, 50_000, 200_000])
    sizes = parser.parse_args().sizes
    results = {}
    with tempfile.TemporaryDirectory(prefix="vigente-scale-") as tmp:
        for size in sizes:
            print(f"{size} memorias…", flush=True)
            results[size] = measure(size, Path(tmp))
    labels = [f"{size:,}".replace(",", ".") for size in sizes]
    bar_chart(
        CHART,
        "Latencia p50 en ms según el número de memorias (sin el modelo)",
        [f"{label} memorias" for label in labels],
        {
            "recall": [results[s]["recall_p50"] for s in sizes],
            "remember (fact)": [results[s]["remember_p50"] for s in sizes],
            "forget": [results[s]["forget_p50"] for s in sizes],
        },
        as_percent=False,
    )
    lines = [
        "# Latencia y escala",
        "",
        "Generado por `eval/scale.py`. Vectores aleatorios de 768 dimensiones (las de "
        "embeddinggemma): mide el coste de Vigente sin el del modelo de embeddings.",
        "",
        f"- Fecha: {datetime.now(UTC):%Y-%m-%d}",
        f"- Máquina: {platform.machine()}, {platform.system()} {platform.release()}, "
        f"Python {platform.python_version()}",
        f"- Repeticiones por tamaño: {RECALLS} búsquedas, {REMEMBERS} escrituras de hechos "
        f"(con detección de posibles reemplazos), {FORGETS} borrados.",
        "",
        "| Memorias | recall p50 / p95 | remember p50 / p95 | forget p50 / p95 | "
        "Carga por memoria | Tamaño en disco |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for size, label in zip(sizes, labels, strict=True):
        r = results[size]
        lines.append(
            f"| {label} | {r['recall_p50']:.1f} / {r['recall_p95']:.1f} ms | "
            f"{r['remember_p50']:.1f} / {r['remember_p95']:.1f} ms | "
            f"{r['forget_p50']:.1f} / {r['forget_p95']:.1f} ms | {r['load_ms']:.2f} ms | "
            f"{r['size_mb']:.0f} MB |"
        )
    lines += [
        "",
        "![Latencia según el número de memorias](scale.svg)",
        "",
        "A esto hay que sumar el modelo de embeddings: una consulta con embeddinggemma en "
        "Ollama tarda unos 15 ms (p50, [elección del modelo](embedding_choice.md)), y en lote "
        "procesa unos 65 textos por segundo en esta máquina.",
    ]
    text = "\n".join(lines) + "\n"
    RESULTS_MD.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
