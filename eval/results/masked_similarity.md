# Propuestas de reemplazo con los valores tapados

Generado por `eval/masked_similarity.py` con `embeddinggemma`, sobre el conjunto escrito a mano del mini-benchmark: 18 actualizaciones, 10 frases del mismo tema y 8 sin relación. Se proponen como mucho 3 hechos, como en `remember`.

Ejemplo: «Ana vive en Madrid.» se compara como «Ana vive en X.».

Cada variante, con su mejor umbral. El umbral actual de Lastword es 0.51.

| Similitud | Umbral | F2 | Precisión | Recall | Frases del mismo tema con propuesta |
|---|---:|---:|---:|---:|---:|
| completa (la de Lastword) | 0.51 | 0.859 | 63% | 94% | 10 de 10 |
| con los valores tapados | 0.61 | 0.750 | 54% | 83% | 7 de 10 |
| media de las dos | 0.56 | 0.842 | 59% | 94% | 9 de 10 |

Decisión en el [ADR-0004](../../docs/decisions/0004-modelo-de-embeddings.md): se mantiene la similitud completa.
