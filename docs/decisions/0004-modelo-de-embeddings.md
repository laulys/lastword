# ADR-0004: embeddinggemma como modelo de embeddings por defecto, con umbral 0.51

- Estado: aceptado
- Fecha: 18-11-2026

## Contexto

Vigente usa embeddings para dos cosas:

1. Proponer posibles reemplazos al guardar un hecho nuevo, aunque no comparta palabras con el anterior («Migramos a SQLite» frente a «El proyecto usa PostgreSQL»).
2. Buscar por significado.

Hacía falta un modelo local, multilingüe (el contenido mezcla español e inglés) y ligero. Los candidatos en Ollama eran `qwen3-embedding:0.6b` (639 MB, 1024 dimensiones) y `embeddinggemma` (622 MB, 768 dimensiones).

## Cómo se decidió

Con un mini-benchmark propio, escrito antes de elegir y ejecutado con el código real de Vigente: [`eval/embedding_choice.py`](../../eval/embedding_choice.py) sobre [`eval/data/embedding_choice.json`](../../eval/data/embedding_choice.json). [Resultados completos](../../eval/results/embedding_choice.md).

El criterio para el umbral se fijó **antes** de ver los datos: el de mejor F1 y, si hay empate, el más alto.

| | BM25 (hito 1) | qwen3-embedding:0.6b | embeddinggemma |
|---|---:|---:|---:|
| Reemplazos: F1 (precisión / recall) | 0.43 (33% / 61%) | 0.73 (62% / 89%) | **0.76** (63% / 94%) |
| Umbral elegido | sin umbral | 0.45 | 0.51 |
| Búsqueda solo vector: Recall@1 | 70% (texto) | 95% | **100%** |
| Búsqueda híbrida: Recall@1 | | 85% | **90%** |
| Latencia de una consulta (p50) | | 19 ms | **15 ms** |
| Tamaño del vector | | 1024 | **768** |

## Decisión

- Modelo por defecto: **embeddinggemma**, con el umbral de posibles reemplazos en **0.51**. Para quien prefiera qwen, su perfil usa 0.45.
- La frase nueva se compara con el formato de *documento*, que dio mejor F1 que el de *similitud* en los dos modelos.
- `min_recall_similarity` queda en **0**: ningún umbral separó las preguntas con respuesta de las que no tienen.

## Consecuencias y limitaciones

- ✅ Los embeddings resuelven el caso que motivó el hito: el test de integración con Ollama comprueba que «Migramos a SQLite para el MVP» propone exactamente el hecho de PostgreSQL.
- ⚠️ **Muestra pequeña.** 18 actualizaciones y 20 preguntas: la diferencia entre modelos es de uno o dos casos. Sirve para elegir entre dos candidatos, no como resultado general.
- ⚠️ **Umbral frágil con gemma.** Las actualizaciones bajan hasta 0.49 de similitud y las frases sin relación llegan a 0.48. Con 0.48 aparece una propuesta falsa; con 0.52 se pierde recall. Qwen tiene una curva más plana y es más estable ante el umbral.
- ⚠️ **El criterio no tiene en cuenta que los errores no cuestan igual.** No detectar un reemplazo deja un dato obsoleto vigente, que es justo lo que el proyecto quiere evitar. Proponer de más solo añade una sugerencia que el agente descarta. Un criterio con más peso para el recall (F2) estaría justificado, pero cambiarlo después de ver los datos sería mover la portería. Se decide antes de la recalibración del Hito 4.
- ⚠️ **Las frases del mismo tema reciben propuestas** con los dos modelos (10 de 10 con gemma). La similitud no distingue «actualiza» de «añade información». Confirma el ADR-0003: la decisión es del agente.
- 🔁 **Pendiente para el Hito 4:**
  - Recalibrar el umbral con más datos.
  - Comparar la fusión híbrida con solo vectores y con RRF con pesos, usando preguntas que incluyan coincidencias exactas.
