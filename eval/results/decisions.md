# Decisiones del hito 4

Generado por `eval/decisions.py` aplicando las reglas de [`eval/README.md`](../README.md) a [E1](e1_synthetic.md) y [E2](e2_longmemeval.md).

## Modo de búsqueda por defecto

| Modo | MRR en E1 | MRR en E2 | Media |
|---|---:|---:|---:|
| texto | 0.632 | 0.882 | 0.757 |
| vector | 0.969 | 0.956 | 0.963 |
| híbrida | 0.884 | 0.947 | 0.915 |
| híbrida ponderada | 0.952 | 0.950 | 0.951 |

**Cambia a «vector»**: su MRR medio supera al de «híbrida» en 0.047.

## Umbral de posibles reemplazos

Mejor F2 en E1: 0.71. Tras validar con el conjunto escrito a mano: **0.51** (se mantiene). Detalle en E1.
