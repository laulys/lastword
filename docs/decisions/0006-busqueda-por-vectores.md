# ADR-0006: La búsqueda por defecto pasa a ser solo por vectores

- Estado: aceptado
- Fecha: 18-11-2026
- Sustituye en parte: la búsqueda híbrida por defecto (arquitectura, §6)

## Contexto

Desde el hito 2, `recall` fusionaba BM25 y vectores con RRF. El mini-benchmark del ADR-0004 ya apuntaba a que solo vectores era mejor, pero con 20 preguntas escritas sin coincidencias exactas no bastaba para cambiarlo. Se dejó para el hito 4, con una regla fijada antes de medir: **gana el modo con mayor MRR medio entre E1 y E2; si la mejora sobre la híbrida es menor de 0.01, se queda la híbrida** ([protocolo](../../eval/README.md)).

## Evidencia

[Resultados completos](../../eval/results/decisions.md), sobre [E1](../../eval/results/e1_synthetic.md) (129 preguntas sintéticas que nombran personas y proyectos) y [E2](../../eval/results/e2_longmemeval.md) (470 preguntas de LongMemEval):

| Modo | MRR en E1 | MRR en E2 | Media |
|---|---:|---:|---:|
| texto (BM25) | 0.632 | 0.882 | 0.757 |
| **vector** | **0.969** | **0.956** | **0.963** |
| híbrida (RRF) | 0.884 | 0.947 | 0.915 |
| híbrida ponderada (vector ×2) | 0.952 | 0.950 | 0.951 |

En E2, solo vectores también gana en Recall@5 *all*: 94% frente a 89%. La mayor diferencia está en las preguntas sobre preferencias: 100% frente a 90%.

## Decisión

- **`recall` busca por defecto solo por vectores.**
- **La híbrida y la de solo texto siguen disponibles** con `VIGENTE_SEARCH_MODE=hybrid|text` (o `--search-mode` en la CLI).
- **Sin modelo de embeddings, o si el servicio falla, se sigue cayendo a texto,** con un aviso.
- **En modo vector, BM25 solo se ejecuta como respaldo:** sin embeddings, o si hay memorias sin vector (guardadas con el servicio caído), que la búsqueda por vectores no encontraría. En ese caso se combina con texto y se avisa. Lo detectó la revisión de código.
- **En la híbrida, los vectores tienen prioridad en los empates.** El desempate ya no depende del id: los ULID tienen una parte aleatoria, y los resultados de la evaluación cambiaban entre ejecuciones (lo detectó un gráfico que no cuadraba).

## Consecuencias

- ✅ Mejor precisión en los dos conjuntos, y `recall` hace menos trabajo.
- ✅ Las evaluaciones son reproducibles: dos ejecuciones dan resultados idénticos.
- ⚠️ **Lo que no se midió:** ningún conjunto tiene muchas consultas de coincidencia exacta (códigos de error, identificadores, números de pedido), que es donde BM25 suele aportar. Para ese uso, la híbrida sigue a un ajuste de distancia.
- ⚠️ La fusión no es el problema principal de una memoria. En las preguntas de actualización de LongMemEval, **todos los modos ponen la versión vieja delante de la nueva algo más de la mitad de las veces** (54–56%). Ninguna forma de buscar distingue lo vigente de lo obsoleto. Eso lo resuelven los reemplazos, no la búsqueda (E1, memoria ingenua: entre el 33% y el 51% de primeros resultados obsoletos según el modo; con reemplazos, 0%).
