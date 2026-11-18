# Latencia y escala

Generado por `eval/scale.py`. Vectores aleatorios de 768 dimensiones (las de embeddinggemma): mide el coste de Vigente sin el del modelo de embeddings.

- Fecha: 2026-11-18
- Máquina: arm64, Darwin 25.5.0, Python 3.12.16
- Repeticiones por tamaño: 50 búsquedas, 30 escrituras de hechos (con detección de posibles reemplazos), 10 borrados.
- Todas las memorias son hechos: es el peor caso para guardar, porque cada hecho nuevo se compara con todos los vigentes.

| Memorias | recall p50 / p95 | remember p50 / p95 | forget p50 / p95 | Tamaño en disco |
|---:|---:|---:|---:|---:|
| 1.000 | 1.1 / 1.2 ms | 0.9 / 1.7 ms | 1.5 / 2.7 ms | 5 MB |
| 10.000 | 2.3 / 2.5 ms | 2.1 / 2.9 ms | 8.6 / 11.1 ms | 50 MB |
| 50.000 | 6.8 / 7.0 ms | 7.1 / 11.9 ms | 42.7 / 48.5 ms | 248 MB |
| 200.000 | 24.5 / 25.7 ms | 26.0 / 27.7 ms | 159.2 / 165.4 ms | 984 MB |

![Latencia según el número de memorias](scale.svg)

A esto hay que sumar el modelo de embeddings, que se mide aparte: la latencia de una consulta con embeddinggemma en Ollama está en [elección del modelo](embedding_choice.md).
