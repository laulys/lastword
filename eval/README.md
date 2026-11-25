# Evaluación

Las cifras de los README y de las tablas de resultados salen de estos scripts, y se publican tal como salen en [`results/`](results/). Unas pocas mediciones sueltas de los ADR y de la arquitectura se hicieron a mano, sin script en el repositorio, y allí se indica: son orientativas.

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
- **Validación:** se compara con el umbral actual (0.51) en el conjunto escrito a mano del hito 2, que no se usa para calibrar en E1. Ese conjunto es el mismo con el que se eligió 0.51, así que la regla favorece al umbral actual: es una regla conservadora, para no cambiar el umbral por unas frases de plantilla, y no una validación independiente.
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

## Protocolo del hito 6: seguridad (E3)

Las reglas y los casos se fijaron **antes** de escribir los detectores. Pero los escribió la misma persona y con los casos a la vista, y comparten vocabulario con ellos («lista de clientes», «no son fiables»): por eso E3 mide un techo, como se explica más abajo. Los tests unitarios usan otros ejemplos.

### Qué se mide

[`e3_security.py`](e3_security.py) pasa cada caso por los detectores de [`safety.py`](../src/vigente/safety.py) y cuenta los aciertos:

| Conjunto | Casos | Lo correcto |
|---|---:|---|
| Secretos de 13 formatos (AWS, GitHub en sus dos formatos, OpenAI, Anthropic, Stripe, Slack, Google, clave privada, JWT, URL con contraseña, contraseña en una frase, token genérico) | 40 | detectarlos |
| Frases parecidas sin secreto (pedidos, hashes, UUID, «la clave del éxito…», «la clave está en 1Password») | 40 | no detectar nada |
| Intentos de que el agente obedezca una memoria (anular reglas, cambiar de rol, ocultar algo al usuario, dictar respuestas, usar herramientas, sacar datos, marcado falso de sistema) | 40 | marcarlos |
| Memorias legítimas que suenan a orden (preferencias, recordatorios, normas del negocio) | 40 | no marcarlas |
| Caracteres invisibles (Unicode *tags*, control bidireccional, ancho cero) | 9 | rechazarlos o marcarlos |
| Texto legítimo con emojis compuestos, acentos, árabe, japonés y símbolos | 9 | no tocarlo |

Los secretos se generan al ejecutar con una semilla fija: el repositorio no contiene nada con forma de clave real.

### Reglas de decisión

- **Secretos.** El detector **rechaza** al guardar solo si detecta al menos el **90%** de los secretos **y** da como secreto como mucho **1 de las 40** frases parecidas (2,5%). Si no cumple las dos condiciones, solo avisa.
- **Caracteres invisibles.** Es un detector determinista, así que la regla es exacta: **todos** los casos ocultos detectados y **ningún** texto legítimo afectado. Si falla, se corrige el detector.
- **Instrucciones incrustadas.** Las memorias marcadas **siempre se guardan**. El aviso al agente en `recall` se activa solo si marca como mucho **4 de las 40** memorias legítimas (10%), porque un aviso que salta a menudo se acaba ignorando. Si marca más, las marcas solo se ven con `vigente review`. La tasa de detección se publica tal cual, sin umbral.
- Se mide una vez. Si una regla obliga a corregir un detector, se publican las dos mediciones y el cambio.
- **Ajuste tras probarlo con datos reales** (fuera del protocolo): con los 89.539 mensajes de usuario de LongMemEval, la primera versión rechazaba 38 como secreto, y 35 eran rutas de URL que parecían tokens. Se corrigió la regla de tokens: dentro de una URL solo cuentan los parámetros con nombre de secreto (`?token=`, `?key=`…), y las rutas absolutas, las huellas (`SHA256:…`) y los trozos de código no cuentan. Ahora rechaza 3, uno de ellos un token real dentro de una URL. E3 dio exactamente el mismo resultado antes y después.

### Sesgo que hay que tener en cuenta

Los casos y los detectores los escribió la misma persona. Aunque los casos se escribieron antes, quien atacara conociendo las reglas las esquivaría con más facilidad: la detección medida es **un techo**, no la que tendría frente a un atacante real. La defensa de fondo sigue siendo que el agente trate la memoria como datos.

## Protocolo: búsquedas exactas (E4)

Las evaluaciones anteriores apenas tenían consultas que dependieran de un código exacto, que es donde la búsqueda por texto suele aportar. E4 lo mide, con reglas fijadas antes de implementar el modo nuevo:

- **Datos:** [`exact_cases.py`](exact_cases.py), 36 memorias de 7 tipos (errores, pedidos, facturas, incidencias, versiones, productos y envíos) que se parecen mucho entre sí y solo cambian en el código («el error E-4031…» frente a «el error E-4013…»), más 15 memorias del mismo negocio sin códigos. Cada pregunta nombra un código: acierta si devuelve su memoria.
- **Modos:** texto, vector, híbrida y **automática**. La automática busca por vectores, salvo que la consulta tenga un código, y entonces combina con texto (híbrida). Un código es una palabra de al menos 3 caracteres con alguna cifra, salvo años (1900–2099) y fechas: E-4031, 7781, F-2026-0117, 2.4.1. La excepción de años y fechas se añadió antes de medir, al ver que «¿dónde vivía Ana en 2025?» habría pasado por código.
- **Métricas:** Recall@1 y MRR.
- **Regla:** el modo automático pasa a ser el de por defecto si **(a)** en E4 su MRR supera al de vector en al menos 0.05 **y (b)** en E1 y en E2 su MRR no queda más de 0.005 por debajo del de vector. Si no, se queda vector.

## Protocolo: búsqueda en dos pasos

La búsqueda por vectores era exacta y su coste crecía de forma lineal ([escala](results/scale.md)). La alternativa: buscar con los vectores comprimidos a 1 bit por dimensión (96 bytes en vez de 3 KB) para elegir candidatos, con los filtros dentro de la búsqueda, y reordenarlos con los vectores exactos.

- **Prueba previa**, hecha a mano con los 89.539 mensajes reales de LongMemEval y sus 470 preguntas: reordenando 100 candidatos se recupera el 97,9% del top 10 exacto, y el primer resultado coincide en el 100% de las preguntas. Recorrer los vectores binarios cuesta 5,5 ms; leer los candidatos exactos desde sqlite-vec, 15 ms, y desde una tabla SQLite normal, 0,1 ms. De ahí el diseño: binarios en sqlite-vec y exactos en una tabla normal.
- **Regla:** se adopta si **(a)** el MRR de E1 y el de E2 no bajan más de 0.005 y **(b)** con 50.000 memorias, `recall` es al menos un 30% más rápido que la búsqueda exacta (p50 de [`scale.py`](scale.py)). Si no, se queda la exacta.

## Protocolo: quién puede cambiar un hecho (E5)

Fijado antes de implementar el [ADR-0011](../docs/decisions/0011-quien-puede-cambiar-un-hecho.md).

- **Datos:**
  - los 12 pares de [`false_replacement.py`](false_replacement.py), escritos antes de ver el resultado;
  - los 72 pares de *knowledge-update* de LongMemEval, un conjunto público: el mensaje más antiguo con evidencia hace de hecho verdadero y el más reciente, de cambio. No son mentiras, pero se parecen como se parecen un dato y su cambio en una conversación real;
  - las 10 frases del mismo tema y las 8 sin relación de [`embedding_choice.json`](data/embedding_choice.json), escritas mucho antes, para contar avisos de más.
- **Vías, con el hecho verdadero guardado por una persona y el ataque de un agente o de un origen externo:** `supersede` con texto, `remember` y después `supersede` con `new_id`, `remember` solo, reenlazar dos hechos de la persona, y `forget` seguido de `remember`. Los pares de cada conjunto comparten un mismo workspace, así que compiten entre sí.
- **Se mide, con embeddinggemma y también sin embeddings:** cuántos hechos de la persona quedan cerrados o borrados; cuántas veces `recall` (k = 5 y k = 1) devuelve el falso sin el verdadero, y sin ningún aviso; y cuántas frases legítimas provocan aviso.
- **Sin protección, y también se publica:** un agente que cambia lo que guardó otro agente.
- **Regla.** Se adopta si:
  - **(a)** por `supersede`, `new_id`, reenlace y `forget`, ningún hecho de una persona queda cerrado ni borrado, en ningún caso;
  - **(b)** por `remember`, con embeddings y k = 5, el falso llega acompañado del verdadero y con aviso en al menos el 90% de los pares de cada conjunto;
  - **(c)** entre orígenes iguales, todos los cambios funcionan como antes;
  - **(d)** el MRR de E1 y el de E2 no bajan más de 0,005.

**Cambio respecto a la primera versión de este protocolo,** también antes de implementar: pedía 30 casos escritos por otra persona, que no están disponibles. Se sustituyen por LongMemEval, que es público. Se añaden las vías de reenlace y de borrado, que el diseño no contemplaba.

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
uv run python eval/e3_security.py            # seguridad; con LongMemEval descargado, también texto real
uv run python eval/e4_exact.py               # códigos exactos
uv run python eval/masked_similarity.py      # propuestas con los valores tapados (ADR-0004)
uv run python eval/false_replacement.py      # un dato falso reemplaza a uno verdadero (ADR-0011)
```
