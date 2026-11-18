# ADR-0010: Búsqueda en dos pasos: vectores binarios y reordenación exacta

- Estado: aceptado
- Fecha: 18-11-2026
- Sustituye: la búsqueda exacta por fuerza bruta (arquitectura, §6)

## Contexto

La búsqueda por vectores recorría todos los vectores exactos (768 números de 4 bytes cada uno). Su coste crecía de forma lineal, 64 ms con 50.000 memorias, y era una de las limitaciones del README.

sqlite-vec 0.1.9 no tiene índices aproximados (HNSW, IVF). Sí guarda vectores binarios, de 1 bit por dimensión, con los mismos filtros dentro de la búsqueda: workspace, tipo y fechas.

## Prueba previa

Hecha a mano, sin script en el repositorio, con los 89.539 mensajes reales de LongMemEval y sus 470 preguntas:

| Candidatos que se reordenan | Top 10 exacto recuperado | Mismo primer resultado | p50 |
|---:|---:|---:|---:|
| exacta | 100% | 100% | 41,3 ms |
| 50 | 94,9% | 100% | 17,7 ms |
| 100 | 97,9% | 100% | 21,4 ms |
| 200 | 99,3% | 100% | 28,6 ms |

Recorrer los vectores binarios costaba solo 5,5 ms. El resto era leer los candidatos exactos desde sqlite-vec: 15 ms para 100. Desde una tabla SQLite normal, la misma lectura cuesta 0,1 ms. Eso decidió el diseño.

## Decisión

- **Dos tablas por espacio vectorial:**
  - `vecb_N`, en sqlite-vec: los vectores binarios (96 bytes), con los filtros dentro de la búsqueda, para elegir candidatos;
  - `vecx_N`, una tabla normal: los vectores exactos, para reordenarlos con la distancia coseno.
- **Se reordenan al menos 100 candidatos** (`RERANK_MIN`), o cinco veces los que se piden si son más. Si los filtros dejan menos de 100 memorias, entran todas y el resultado es exacto: con memorias pequeñas no cambia nada.
- **La similitud que se devuelve es la exacta,** así que los umbrales del ADR-0004 siguen valiendo.
- **Migración v4:** convierte las bases existentes sin recalcular embeddings. Los vectores exactos pasan a la tabla normal y se cuantizan para la binaria.
- **Los vectores con un número de dimensiones que no es múltiplo de 8** se rellenan con ceros, solo en la versión binaria.

## Evidencia

Con la regla fijada antes de medir ([protocolo](../../eval/README.md)):

- **(a) Calidad:** el MRR de E1 (0,969) y el de E2 (0,956) son idénticos con la búsqueda exacta y con la de dos pasos. E1 sí pone a prueba la aproximación: reordena 100 candidatos de hasta 228 memorias. E2 la pone a prueba poco: guarda 244 mensajes de media por pregunta y pide 50 resultados, así que reordena 250 candidatos y en 328 de las 470 preguntas entran todos, es decir, la búsqueda es exacta. La evidencia de que aproximar no cambia el primer resultado es sobre todo la prueba previa. (La primera versión de este ADR decía «unas 500 memorias, de las que se reordenan 100»: era un error.)
- **(b) Velocidad** ([escala](../../eval/results/scale.md), p50, sin el modelo de embeddings):

| Memorias | `recall` antes | `recall` ahora | `remember` antes | `remember` ahora |
|---:|---:|---:|---:|---:|
| 10.000 | 12,6 ms | 2,3 ms | 11,7 ms | 2,1 ms |
| 50.000 | 63,6 ms | 6,8 ms | 57,6 ms | 7,1 ms |
| 200.000 | — | 24,5 ms | — | 26,0 ms |

Estas cifras se midieron de nuevo en la v1.3, con la misma prueba sobre la v1.1.0 («antes») y sobre la v1.3 («ahora»). La primera medición llenaba la base de episodios, con los que un hecho nuevo no se compara, y daba para `remember` 1,3 ms con 50.000 memorias: mejor de lo que es. Ahora la base es de hechos. «Ahora» incluye además un índice de la v1.3 que ahorra a `recall` unos 2 ms con 50.000 memorias.

## Alternativas descartadas

- **Un índice aproximado externo** (hnswlib, FAISS): rompería el «un solo archivo» y las transacciones que cubren a la vez filas, texto y vectores (ADR-0001).
- **Reordenar leyendo de sqlite-vec:** solo era 1,9 veces más rápido, por el coste de leer los candidatos.
- **Vectores en `int8`** (4 veces más pequeños): menos ganancia que los binarios, y la reordenación exacta ya recupera la precisión.

## Consecuencias

- ✅ Con 50.000 memorias, `recall` es unas 9 veces más rápido y `remember` unas 8, porque guardar un hecho también busca sus posibles reemplazos.
- ✅ Los resultados de E1 y E2 no cambian.
- ⚠️ La base ocupa alrededor de un 30% más en disco: 248 MB frente a 190 MB con 50.000 memorias.
- ⚠️ Sigue recorriendo todos los vectores, aunque comprimidos: el coste crece de forma lineal, con una pendiente unas nueve veces menor. Con millones de memorias haría falta un índice aproximado, que sqlite-vec todavía no tiene.
