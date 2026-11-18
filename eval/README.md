# Evaluación

Todo lo que el proyecto afirma sobre calidad o rendimiento sale de estos scripts. Los resultados se publican tal como salen en [`results/`](results/).

## Protocolo del hito 4

Las reglas de decisión se fijaron **antes** de ejecutar los experimentos y los scripts las aplican tal cual. Así los datos no pueden mover la portería.

### E1. Sintético: ¿los reemplazos evitan datos obsoletos?

[`e1_synthetic.py`](e1_synthetic.py), con datos de [`synthetic.py`](synthetic.py) (semilla fija, en español):

- **Datos:** 72 atributos (ciudad de una persona, base de datos de un proyecto…) con 1 a 4 versiones fechadas, 54 episodios de distracción y 129 preguntas, sobre el presente o sobre una fecha pasada.
- **Sistemas, con la misma búsqueda:**
  - **ingenua:** cada versión queda activa;
  - **ingenua + recencia:** reordena los 5 primeros por fecha;
  - **Vigente:** cada versión reemplaza a la anterior. La ingesta es *oráculo*: se usa el reemplazo verdadero, para medir el mecanismo aislado de las decisiones del agente.
- **Métricas:** Recall@1, Recall@5, MRR y **tasa de obsoletos**: el primer resultado (o alguno de los cinco primeros) es otra versión del mismo dato.
- **Regla de la tesis:** se sostiene si, con la búsqueda por defecto, Vigente baja la tasa de obsoletos en el primer resultado respecto a la ingenua **y** su Recall@5 no empeora más de 2 puntos.

### Umbral de posibles reemplazos

- **Calibración:** durante la ingesta de E1 se registran, para cada hecho nuevo, sus 3 candidatos más parecidos con su similitud. Se elige el umbral con **mejor F2**, que pesa el doble no perder un reemplazo, porque un reemplazo perdido deja un dato obsoleto vigente y una propuesta de más solo añade una sugerencia que el agente descarta (ADR-0004).
- **Validación:** se compara con el umbral actual (0.51) en el conjunto escrito a mano del hito 2, que no se usa para calibrar.
- **Regla:** se adopta el nuevo umbral solo si su F2 en la validación es mayor o igual que el de 0.51.

### E2. LongMemEval: búsqueda sobre conversaciones reales

[`e2_longmemeval.py`](e2_longmemeval.py), con [`longmemeval.py`](longmemeval.py) para cargar los datos y [`download_longmemeval.py`](download_longmemeval.py) para descargarlos:

- **Preguntas:** 470 (las 30 de abstención quedan fuera, como en el propio benchmark).
- **Ingesta:** cada mensaje del usuario se guarda como `episode` con la fecha de su sesión, recortado a 4.000 caracteres si hace falta.
- **Métricas por sesión:** Recall@5 *any* (alguna sesión con evidencia entre las 5 primeras), Recall@5 *all* (todas) y MRR.
- **Modos comparados:** texto, vector, híbrida (RRF) e híbrida ponderada (vector ×2).
- En las preguntas de *knowledge-update* (dos sesiones con evidencia, una vieja y una nueva) se mide **cuántas veces la búsqueda pone la vieja delante de la nueva**. Es el problema que resuelven los reemplazos, medido en un benchmark público.
- **Ajuste descubierto al ejecutar** (no depende de los resultados): en 69 preguntas hay sesiones fechadas hasta 24 h después de la pregunta. Como LongMemEval responde después de todo el historial, se busca en la fecha más tardía entre la pregunta y su última sesión. Buscar en la fecha exacta ocultaría esa evidencia con el filtro temporal.

### Modo de búsqueda por defecto

- **Regla:** gana el modo con mayor MRR medio entre E1 (Vigente, todas las preguntas) y E2 (sesión). Si la diferencia con la híbrida es menor de 0.01, se queda la híbrida.
- Las reglas se aplican en [`decisions.py`](decisions.py), que lee los resultados de E1 y E2.

### Fuera de alcance

**E2b**, un agente simulado con un modelo local procesando LongMemEval, queda fuera: llevaría horas de cómputo y mediría sobre todo al modelo local. El protocolo con un agente real ya se demostró en [`agent_demo.py`](agent_demo.py).

## Reproducibilidad

E1 y E2 dan resultados idénticos en ejecuciones repetidas, comprobado comparando las salidas de dos ejecuciones. Para conseguirlo hubo que quitar el desempate por id en la fusión, porque los ULID tienen una parte aleatoria. Las latencias de `scale.py` varían unas décimas de milisegundo entre ejecuciones.

## Cómo repetirlo

```bash
uv run python eval/download_longmemeval.py   # 277 MB, verificado con SHA-256
uv run python eval/longmemeval.py            # embeddings en caché (unos 25 min la primera vez)
uv run python eval/e1_synthetic.py
uv run python eval/e2_longmemeval.py
uv run python eval/decisions.py
uv run python eval/scale.py                  # latencia y escala
```
