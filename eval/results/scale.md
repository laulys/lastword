# Latencia y escala

Generado por `eval/scale.py`. Vectores aleatorios de 768 dimensiones (las de embeddinggemma): mide el coste de Vigente sin el del modelo de embeddings.

- Fecha: 2026-11-18
- Máquina: arm64, Darwin 25.5.0, Python 3.12.16
- Repeticiones por tamaño: 50 búsquedas, 30 escrituras de hechos (con detección de posibles reemplazos), 10 borrados.

| Memorias | recall p50 / p95 | remember p50 / p95 | forget p50 / p95 | Carga por memoria | Tamaño en disco |
|---:|---:|---:|---:|---:|---:|
| 1.000 | 0.7 / 0.8 ms | 0.4 / 1.0 ms | 1.3 / 1.9 ms | 0.28 ms | 5 MB |
| 10.000 | 2.2 / 2.3 ms | 0.5 / 0.6 ms | 7.2 / 9.0 ms | 0.29 ms | 49 MB |
| 50.000 | 8.5 / 8.9 ms | 1.3 / 1.5 ms | 36.5 / 40.6 ms | 0.30 ms | 247 MB |
| 200.000 | 36.5 / 37.0 ms | 4.4 / 5.0 ms | 146.1 / 151.8 ms | 0.30 ms | 979 MB |

![Latencia según el número de memorias](scale.svg)

A esto hay que sumar el modelo de embeddings: una consulta con embeddinggemma en Ollama tarda unos 15 ms (p50, [elección del modelo](embedding_choice.md)), y en lote procesa unos 65 textos por segundo en esta máquina.
