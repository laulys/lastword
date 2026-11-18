# Elección del modelo de embeddings

Generado por `eval/embedding_choice.py` con `eval/data/embedding_choice.json`.
Conjunto pequeño y escrito a mano: 18 actualizaciones, 10 frases del mismo tema que no reemplazan nada, 8 sin relación y 20 preguntas. Sirve para elegir entre candidatos, no como resultado general.

- Fecha: 2026-11-18
- Máquina: arm64, Darwin 25.5.0
- Python 3.12.16
- Ollama 0.40.15
- `qwen3-embedding:0.6b`: digest ac6da0dfba84
- `embeddinggemma`: digest 85462619ee72

## 1. Detección de posibles reemplazos

Propuestas = los 3 hechos más parecidos con similitud ≥ umbral. Se elige el umbral con mejor F1. Cuenta como error cualquier propuesta que no sea el hecho reemplazado, también ante frases del mismo tema.

| Modelo | Formato de la frase nueva | Umbral | Precisión | Recall | F1 | Mismo tema con propuesta | Sin relación con propuesta |
|---|---|---:|---:|---:|---:|---:|---:|
| BM25 (hito 1) | texto | sin umbral | 33% | 61% | 0.43 | n/a | n/a |
| `qwen3-embedding:0.6b` | document | 0.45 | 62% | 89% | 0.73 | 8/10 | 0/8 |
| `qwen3-embedding:0.6b` | similarity | 0.39 | 59% | 94% | 0.72 | 9/10 | 0/8 |
| `embeddinggemma` | document | 0.51 | 63% | 94% | 0.76 | 10/10 | 0/8 |
| `embeddinggemma` | similarity | 0.30 | 59% | 89% | 0.71 | 10/10 | 0/8 |

Separación: similitud entre cada actualización y el hecho que reemplaza, frente a la mayor similitud de las frases sin relación con cualquier hecho.

| Modelo | Formato | Actualización: media / mínima | Sin relación: media / máxima |
|---|---|---:|---:|
| `qwen3-embedding:0.6b` | document | 0.63 / 0.42 | 0.34 / 0.43 |
| `qwen3-embedding:0.6b` | similarity | 0.57 / 0.34 | 0.31 / 0.39 |
| `embeddinggemma` | document | 0.67 / 0.49 | 0.43 / 0.48 |
| `embeddinggemma` | similarity | 0.44 / 0.28 | 0.23 / 0.27 |

## 2. Búsqueda

Cada pregunta tiene una respuesta entre 38 memorias.

| Modelo | Búsqueda | Recall@1 | Recall@3 | MRR |
|---|---|---:|---:|---:|
| `qwen3-embedding:0.6b` | texto | 70% | 80% | 0.75 |
| `qwen3-embedding:0.6b` | vector | 95% | 95% | 0.96 |
| `qwen3-embedding:0.6b` | híbrida | 90% | 95% | 0.93 |
| `embeddinggemma` | texto | 70% | 80% | 0.75 |
| `embeddinggemma` | vector | 100% | 100% | 1.00 |
| `embeddinggemma` | híbrida | 95% | 100% | 0.97 |

## 3. Preguntas sin respuesta y latencia

| Modelo | Dim. | Similitud top-1 con respuesta: mínima / media | Sin respuesta: media / máxima | Latencia consulta p50 / p95 |
|---|---:|---:|---:|---:|
| `qwen3-embedding:0.6b` | 1024 | 0.36 / 0.66 | 0.46 / 0.58 | 23 / 35 ms |
| `embeddinggemma` | 768 | 0.19 / 0.58 | 0.29 / 0.44 | 13 / 22 ms |
