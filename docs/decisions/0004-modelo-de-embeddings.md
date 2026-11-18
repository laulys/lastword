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

## Revisión en el hito 4

El umbral se volvió a calibrar con E1, el experimento sintético ([resultados](../../eval/results/e1_synthetic.md)). Antes de ejecutarlo se fijó la regla: mejor **F2** en E1, aceptado solo si en el conjunto escrito a mano su F2 no empeora.

- **En E1, el mejor F2 sale con 0.71.**
- **En la validación, 0.71 empeora mucho:** F2 de 0.86 con 0.51 frente a 0.49 con 0.71, porque el recall cae del 94% al 44%.
- **Decisión: se mantiene 0.51.** Las frases de E1 salen de plantillas («Ana vive en…», «Ana trabaja en…») y se parecen entre sí más que las reales. Calibrar con ellas habría sobreajustado el umbral a las plantillas. La regla fijada de antemano lo evitó.
- **Coste conocido:** con 0.51, en E1 la precisión de las propuestas es solo del 22%. Son sugerencias de más que el agente tiene que descartar, sobre todo otros datos de la misma persona o proyecto.
- **Nota sobre la tabla de arriba:** al quitar el desempate por id de la fusión ([ADR-0006](0006-busqueda-por-vectores.md)), la búsqueda híbrida del mini-benchmark sube de 90% a 95% de Recall@1 con embeddinggemma y de 85% a 90% con qwen. Las demás cifras no cambian al repetirlo, y la elección del modelo tampoco.

## Revisión en la v1.2: ¿se puede separar «actualiza» de «añade»?

El README tenía como limitación que las frases del mismo tema también reciben propuestas. Se probó una idea sin LLM: una actualización cambia el valor de un mismo atributo («Ana vive en Madrid» → «Ana se mudó a Lisboa»), y una frase del mismo tema cambia de atributo («Ana trabaja como diseñadora»). Si se tapan los valores (nombres propios y cifras) antes de comparar, la similitud debería medir sobre todo el atributo.

Resultado, con el conjunto escrito a mano y el mejor umbral de cada variante ([script](../../eval/masked_similarity.py), [resultados](../../eval/results/masked_similarity.md)):

| Similitud | F2 | Recall | Frases del mismo tema con propuesta |
|---|---:|---:|---:|
| completa (la actual, 0.51) | **0.859** | 94% | 10 de 10 |
| con los valores tapados | 0.750 | 83% | 7 de 10 |
| media de las dos | 0.842 | 94% | 9 de 10 |

Tapar los valores gana algo de precisión, pero pierde actualizaciones verdaderas, que es lo que más cuesta: un reemplazo perdido deja un dato obsoleto vigente. **Se mantiene la similitud completa.** Con similitud no se puede distinguir «actualiza» de «añade»: por eso decide el agente (ADR-0003), que lee las dos frases y descarta las propuestas que sobran.
