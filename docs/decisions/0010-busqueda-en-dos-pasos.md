# ADR-0010: Búsqueda en dos pasos: vectores binarios y reordenación exacta

- Estado: aceptado
- Fecha: 18-11-2026
- Sustituye: la búsqueda exacta por fuerza bruta (arquitectura, §6)

## Contexto

La búsqueda por vectores recorría todos los vectores exactos (768 números de 4 bytes cada uno). Su coste crecía de forma lineal, 62 ms con 50.000 memorias ([escala](../../eval/results/scale.md)), y era una de las limitaciones del README.

sqlite-vec 0.1.9 no tiene índices aproximados (HNSW, IVF). Sí guarda vectores binarios, de 1 bit por dimensión, con los mismos filtros dentro de la búsqueda: workspace, tipo y fechas.

## Prueba previa

Con los 89.539 mensajes reales de LongMemEval y sus 470 preguntas:

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

- **(a) Calidad:** el MRR de E1 (0,969) y el de E2 (0,956) son idénticos con la búsqueda exacta y con la de dos pasos. En E2 la búsqueda sí aproxima: unas 500 memorias por pregunta, de las que se reordenan 100.
- **(b) Velocidad** ([escala](../../eval/results/scale.md), p50, sin el modelo de embeddings):

| Memorias | `recall` antes | `recall` ahora | `remember` antes | `remember` ahora |
|---:|---:|---:|---:|---:|
| 10.000 | 12,7 ms | 2,2 ms | 5,6 ms | 0,5 ms |
| 50.000 | 62,2 ms | 8,5 ms | 25,2 ms | 1,3 ms |
| 200.000 | — | 36,5 ms | — | 4,4 ms |

## Alternativas descartadas

- **Un índice aproximado externo** (hnswlib, FAISS): rompería el «un solo archivo» y las transacciones que cubren a la vez filas, texto y vectores (ADR-0001).
- **Reordenar leyendo de sqlite-vec:** solo era 1,9 veces más rápido, por el coste de leer los candidatos.
- **Vectores en `int8`** (4 veces más pequeños): menos ganancia que los binarios, y la reordenación exacta ya recupera la precisión.

## Consecuencias

- ✅ `recall` es unas 7 veces más rápido con 50.000 memorias, y `remember` unas 19 veces, porque también busca los posibles reemplazos.
- ✅ Los resultados de E1 y E2 no cambian.
- ⚠️ La base ocupa alrededor de un 30% más en disco: 247 MB frente a 191 MB con 50.000 memorias.
- ⚠️ Sigue recorriendo todos los vectores, aunque comprimidos: el coste crece de forma lineal, con una pendiente siete veces menor. Con millones de memorias haría falta un índice aproximado, que sqlite-vec todavía no tiene.
