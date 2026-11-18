# E4: búsquedas con códigos exactos

Generado por `eval/e4_exact.py` con `embeddinggemma`. 36 preguntas que nombran un código (errores, pedidos, facturas, incidencias, versiones, productos y envíos), sobre memorias que solo se diferencian en el código, más 15 sin códigos.

| Modo | Recall@1 | Recall@5 | MRR |
|---|---:|---:|---:|
| texto | 94% | 100% | 0.972 |
| vector | 100% | 100% | 1.000 |
| híbrida | 100% | 100% | 1.000 |
| automática | 100% | 100% | 1.000 |

El modo **automático** busca por vectores y combina con texto solo cuando la consulta trae un código: una palabra de al menos 3 caracteres con alguna cifra, salvo años y fechas.

## Acierto a la primera, por tipo

| Tipo | texto | vector | híbrida | automática |
|---|---:|---:|---:|---:|
| errores | 100% | 100% | 100% | 100% |
| pedidos | 100% | 100% | 100% | 100% |
| facturas | 60% | 100% | 100% | 100% |
| incidencias | 100% | 100% | 100% | 100% |
| versiones | 100% | 100% | 100% | 100% |
| productos | 100% | 100% | 100% | 100% |
| envíos | 100% | 100% | 100% | 100% |

## Decisión, según la regla fijada antes

- En E4, el MRR del automático supera al de vector en 0.000 (la regla pide al menos 0.05).
- En E1, MRR 0.969 con vector y 0.969 con el automático; en E2, 0.956 y 0.956. La regla permite perder como mucho 0.005 (se pierde 0.000 y 0.000).

**Se queda vector como modo por defecto.**
