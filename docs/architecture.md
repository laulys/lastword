# Vigente — Arquitectura

Estado: **aprobada** (18-11-2026). Los cinco hitos del plan original están implementados, y el hito 6 añade seguridad (§11). Lo marcado con *(Hito N)* recoge lo que se aprendió al implementar ese hito.

## 1. Resumen

Vigente es un servidor MCP local que da memoria persistente a agentes de IA y separa lo que **sigue siendo verdad** de lo que **fue verdad**. Los hechos no se editan ni se borran cuando cambian: se reemplazan con una cadena enlazada y fechas de validez. Por defecto la búsqueda solo devuelve lo vigente, y con `as_of` devuelve lo que era verdad en una fecha.

Todo vive en un único archivo SQLite: filas, índice de texto (FTS5) e índice vectorial (sqlite-vec). Así cualquier escritura o borrado es **una sola transacción** sobre los tres.

## 2. Entorno verificado (18-11-2026)

| Componente | Resultado |
|---|---|
| uv | 0.13.9 (instalado con Homebrew) |
| Python 3.12.16 / 3.13.18 (uv) y 3.15.2 (Homebrew) | Los tres cargan extensiones de SQLite |
| SQLite | 3.53–3.54, FTS5 disponible |
| sqlite-vec | v0.1.9 carga correctamente |
| Ollama | Instalado. Solo tiene `qwen3:8b` (chat); **falta un modelo de embeddings** |

Comportamiento de sqlite-vec comprobado con scripts de prueba:

1. **Filtros dentro del KNN.** Con `workspace_id` como *partition key* y columnas de metadatos (`status`, `valid_from`, `valid_to`), el filtro se aplica *antes* de elegir los `k` vecinos. Prueba: un vecino idéntico de otro workspace y uno reemplazado se excluyeron, y el resultado fue el siguiente más cercano válido. No hace falta sobremuestrear.
2. **`as_of` funciona dentro del KNN** con `valid_from <= t AND valid_to > t`.
3. **`UPDATE` de metadatos funciona.** Cerrar `valid_to` al reemplazar saca la fila de las búsquedas vigentes.
4. **Las columnas de metadatos no aceptan `NULL`.** Hace falta un centinela para «vigente».
5. ⚠️ **Un `INSERT` que falla deja una fila fantasma a medias** en la tabla vectorial si no hay transacción explícita. Con `BEGIN … ROLLBACK` desaparece por completo (verificado).

Los puntos 4 y 5 determinan reglas del diseño (ver §4 y ADR-0001).

## 3. Capas

```
            ┌───────────────┐   ┌──────────┐
            │ mcp_server.py │   │  cli.py  │      interfaces: validan entrada y formatean salida
            └───────┬───────┘   └────┬─────┘
                    └───────┬────────┘
                     ┌──────▼──────┐
                     │ service.py  │             casos de uso, reglas de reemplazo, transacciones
                     └──┬───────┬──┘
           ┌────────────▼┐     ┌▼──────────────┐
           │ retrieval.py│     │ embeddings.py │  FTS, vector, RRF, filtro temporal / Embedder
           └──────┬──────┘     └───────────────┘
           ┌──────▼──────┐
           │  store.py   │                       SQL, migraciones, conexión
           └─────────────┘
   models.py, config.py e i18n.py: compartidos por todos
```

- Las dependencias van en un solo sentido, de arriba abajo.
- Solo `store.py` escribe SQL.
- Solo `service.py` abre transacciones. Una operación del usuario es una transacción.
- `Embedder` es la única interfaz abstracta. Tiene dos implementaciones reales: la compatible con OpenAI y `FakeEmbedder` para tests.
- *(Hito 2)* Cada familia de modelos tiene un `ModelProfile`: el formato de entrada que espera según el uso (documento guardado, pregunta, comparación) y sus umbrales calibrados. Si no se respeta el formato, la calidad cae sin dar ningún error.
- *(Hito 2)* Los embeddings se calculan **fuera** de las transacciones: son llamadas de red y no deben bloquear la base de datos.
- *(v1.2)* Lo que lee una persona (la CLI, los errores, los avisos, `vigente doctor` y el arranque de `vigente-mcp`) pasa por `tr()` de `i18n.py`: el texto fuente está en español y hay un catálogo en inglés. El idioma sale de `VIGENTE_LANG` o del sistema. Lo que lee el agente (instrucciones y descripciones MCP) se queda en español. Un test comprueba que cada texto tiene traducción con los mismos huecos.

## 4. Esquema SQL

Convenciones:

- Fechas en **milisegundos epoch UTC** (`INTEGER`).
- «Vigente» se guarda como `valid_to = 253402300799999` (31-12-9999). Las columnas de metadatos de sqlite-vec no aceptan `NULL`, y usar el mismo centinela en todas las tablas evita dos formas de decir lo mismo. La API pública expone `valid_to: null`; la conversión ocurre solo en `store.py`.
- IDs ULID: ordenables por tiempo y legibles.
- Migraciones con `PRAGMA user_version`.
- Conexión con `autocommit=True` (Python ≥ 3.12) y `BEGIN IMMEDIATE` explícito en cada escritura.
- `journal_mode=WAL` y `busy_timeout`, porque el servidor MCP y la CLI pueden abrir el mismo archivo a la vez. *(v1.0.1)* SQLite no aplica `busy_timeout` al paso a WAL: si varios servidores creaban la base a la vez, alguno fallaba al arrancar. Ese paso se reintenta dentro del mismo plazo.
- `secure_delete=ON`: SQLite sobrescribe con ceros lo que borra, en vez de dejarlo en páginas libres. *(Hito 1)*

```sql
CREATE TABLE workspaces (
  id          TEXT PRIMARY KEY,          -- slug: "portfolio", "trabajo"
  created_at  INTEGER NOT NULL
);

CREATE TABLE memories (
  seq              INTEGER PRIMARY KEY,  -- alias estable de rowid (ver nota)
  id               TEXT NOT NULL UNIQUE, -- ULID, identificador público
  workspace_id     TEXT NOT NULL REFERENCES workspaces(id),
  kind             TEXT NOT NULL CHECK (kind IN ('fact', 'episode')),
  content          TEXT NOT NULL CHECK (length(content) BETWEEN 1 AND 4000),
  tags             TEXT NOT NULL DEFAULT '[]',          -- array JSON
  source           TEXT,                                -- "mcp", "cli", ...
  confidence       REAL CHECK (confidence BETWEEN 0 AND 1),
  created_at       INTEGER NOT NULL,     -- cuándo se registró (tiempo de registro)
  valid_from       INTEGER NOT NULL,     -- desde cuándo es verdad (tiempo de validez)
  valid_to         INTEGER NOT NULL DEFAULT 253402300799999,
  status           TEXT NOT NULL CHECK (status IN ('active', 'superseded')),
  supersedes_id    TEXT REFERENCES memories(id),
  superseded_by_id TEXT REFERENCES memories(id),
  supersede_reason TEXT,
  embedding_model  TEXT,                 -- NULL si no tiene vector
  CHECK (valid_from < valid_to),
  CHECK (kind = 'fact' OR (supersedes_id IS NULL AND superseded_by_id IS NULL))
);
CREATE INDEX memories_ws_status ON memories(workspace_id, status);
CREATE INDEX memories_ws_validity ON memories(workspace_id, valid_from, valid_to);
-- v3 y v6: seguir la cadena de versiones en los dos sentidos sin recorrer la tabla.
CREATE INDEX memories_superseded_by ON memories(superseded_by_id) WHERE superseded_by_id IS NOT NULL;
CREATE INDEX memories_supersedes ON memories(supersedes_id) WHERE supersedes_id IS NOT NULL;
-- v5: ¿a alguna memoria le falta el vector del modelo en uso? Se pregunta en cada búsqueda.
CREATE INDEX memories_ws_embedding ON memories(workspace_id, embedding_model);

-- Texto: tabla FTS5 propia (no external-content), con rowid = memories.seq.
-- Duplica el texto, pero el borrado es un DELETE normal y no depende de triggers.
CREATE VIRTUAL TABLE memories_fts USING fts5(
  content,
  tags,
  tokenize = 'unicode61 remove_diacritics 2'   -- "migración" = "migracion"; sin stemming (ES + EN)
);

-- Migración v2 (Hito 2). Registro de espacios vectoriales: un índice por (modelo, dimensión).
-- El espacio activo es el del modelo configurado; no hace falta una columna "is_active".
CREATE TABLE embedding_spaces (
  id          INTEGER PRIMARY KEY,
  model       TEXT NOT NULL,
  dim         INTEGER NOT NULL CHECK (dim BETWEEN 1 AND 8192),
  table_name  TEXT NOT NULL UNIQUE,      -- "vecb_1", "vecb_2", ...
  created_at  INTEGER NOT NULL,
  UNIQUE (model, dim)
);

-- Dos tablas por espacio, creadas al guardar su primer vector (ejemplo: embeddinggemma,
-- 768 dimensiones). Desde la v1.2 la búsqueda va en dos pasos (ADR-0010).
-- vecb_N: vectores de 1 bit por dimensión, con los filtros, para elegir candidatos.
-- rowid = memories.seq, igual que en FTS.
CREATE VIRTUAL TABLE vecb_1 USING vec0(
  workspace_id  TEXT PARTITION KEY,
  kind          TEXT,
  valid_from    INTEGER,
  valid_to      INTEGER,
  embedding     BIT[768]
);
-- vecx_N: los vectores exactos, en una tabla normal, para reordenar los candidatos.
CREATE TABLE vecx_1 (
  seq        INTEGER PRIMARY KEY REFERENCES memories(seq),
  embedding  BLOB NOT NULL
);
```

**Por qué `seq` además del ULID** *(Hito 1)*: el índice FTS se enlaza por número de fila. En tablas sin `INTEGER PRIMARY KEY`, `VACUUM` puede renumerar el rowid implícito, y eso desincronizaría el índice en silencio. `seq` es un alias explícito y estable; `id` sigue siendo el identificador público.

**Por qué hay columnas duplicadas en `vecb_N`:** `kind`, `valid_from` y `valid_to` se repiten ahí para que el filtro ocurra dentro del KNN (§2.1). *(Hito 2)* No hace falta `status`, porque la vigencia se decide por fechas (§5). Se mantienen sincronizadas en la misma transacción que `memories`, y `vigente doctor` comprueba que coincidan y que no haya vectores huérfanos.

*(Hito 2)* **Borrar un vector también lo borra del disco:** sqlite-vec sobrescribe el vector dentro de su bloque al borrarlo y, con `secure_delete`, no quedan restos. Un test busca en el archivo los bytes del vector exacto y los de su copia binaria, antes y después de `forget`. Importa porque un embedding puede revelar información del texto original.

**Invariante comprobada por tests y por `vigente doctor`:** el conjunto de ids de `memories`, `memories_fts` y el espacio vectorial activo es el mismo (salvo memorias sin embedding), y cada vector binario tiene su pareja exacta.

## 5. Reglas de dominio

- **Reemplazar** (`supersede(old, new_content, valid_from=t)`), en una sola transacción:
  1. Inserta `new` con `valid_from = t` y `supersedes_id = old`. Si no se indica `t`, se usa `max(ahora, old.valid_from + 1 ms)`, para que un reemplazo en el mismo milisegundo no falle. El motivo (`reason`) se guarda en `new`. *(Hito 1)*
  2. Actualiza `old` con `valid_to = t`, `status = superseded` y `superseded_by_id = new`.
  3. Actualiza los mismos campos en `vecb_N` y añade `new` a FTS y a las dos tablas de vectores.
- **Solo se reemplazan `fact`.** Los `episode` («el 3 de mayo decidimos X») son registros de algo que pasó y no dejan de ser verdad. Por eso un `episode` tampoco puede tener `valid_from` en el futuro. *(Hito 1)*
- **«Vigente en t»** significa `valid_from <= t < valid_to`, igual para hechos y episodios. `status` solo indica si la memoria tiene sucesora; la búsqueda filtra por fechas. Así, un hecho con `valid_from` futuro («desde enero el responsable será Luis») no aparece hasta que empieza. *(Hito 1)*
- **Reemplazar algo ya reemplazado** devuelve un error con el id de su sucesor actual, para que el agente reintente sobre la versión vigente. Evita bifurcar la cadena.
- **Un hecho nuevo puede reemplazar a varios** (`also_old_ids`): «Ana y Luis se mudaron a Lisboa» cierra la ciudad de los dos. Cada hecho viejo apunta a la versión nueva con `superseded_by_id`, y la nueva guarda el principal en `supersedes_id`. Es todo o nada, y el historial recorre todas las versiones conectadas. A una versión que ya reemplazó un hecho se le pueden sumar otros después, con `new_id`. Reglas y alternativas en el [ADR-0008](decisions/0008-un-hecho-reemplaza-a-varios.md).
- *(Hito 3)* **Enlazar en vez de crear:** `supersede` también acepta `new_id`, el id de una memoria ya guardada, normalmente la que `remember` acaba de guardar y que propuso como reemplazo. Sin esto, el flujo natural del agente dejaba el hecho duplicado. Reglas y alternativas en el [ADR-0005](decisions/0005-dos-formas-de-reemplazar.md).
- **Olvidar** (`forget(id)`) borra **toda la cadena** del hecho (todas sus versiones, también las de los hechos que una versión unió) de `memories`, FTS y vector, y devuelve los ids borrados. Si alguien pide olvidar dónde vive, también debe desaparecer dónde vivía. Si otra conexión está leyendo la base, SQLite no puede vaciar su registro de cambios (WAL): el borrado es efectivo, pero el texto sigue en ese archivo hasta que la conexión acabe, y `forget` lo avisa.
  - *(Hito 1)* Para que desaparezca también del disco:
    - `secure_delete=ON` sobrescribe las filas borradas.
    - Un `optimize` del índice FTS: FTS5 registra los borrados como marcas que solo aplica al fusionar segmentos, así que sin esto los términos sobreviven en segmentos antiguos.
    - Un checkpoint del WAL.
  - Un test lo verifica buscando el texto en los bytes del archivo, con control previo de que estaba ahí.
  - Coste: `optimize` reescribe el índice entero. Se medirá en el Hito 4.
- **Tiempo de registro y tiempo de validez son distintos.** `created_at` dice cuándo lo supimos; `valid_from`/`valid_to` dicen cuándo fue verdad. `as_of` consulta el tiempo de validez. Ejemplo: «me mudé a Lisboa en marzo», dicho en mayo, tiene `valid_from = marzo` y `created_at = mayo`.
- **Aislamiento.** El workspace lo fija la configuración del servidor (`VIGENTE_WORKSPACE`). No es un parámetro de las herramientas MCP. Todas las consultas de `store.py` lo exigen como argumento obligatorio.

## 6. Recuperación

1. **Saneamiento de la consulta FTS.** El texto del usuario no va directo a `MATCH`, porque comillas, `*`, `NEAR` o `-` romperían la sintaxis o cambiarían su significado. Se tokeniza y cada token se cita: `"token1" OR "token2"`. Se quitan palabras vacías (lista mínima en español e inglés) salvo que no quede ninguna otra. El ranking lo da BM25.
2. **FTS:** `memories_fts` unida a `memories` con los filtros de workspace, vigencia o `as_of`, y `kind`. *(Hito 4)* La unión es un `CROSS JOIN` para obligar a SQLite a recorrer primero el índice FTS. Con un `JOIN` normal, el planificador elegía recorrer `memories` por su índice y ejecutar la búsqueda de texto una vez por fila, con coste cuadrático: 432 ms con 2.000 memorias frente a 1,9 ms después, con los mismos resultados. Lo encontró el benchmark de escala, y un test comprueba el plan de la consulta. Se piden `max(k × 4, 20)` candidatos, con un máximo de 200.
3. **Vector:** se calcula el embedding de la consulta con el formato de pregunta del modelo. *(v1.2)* La búsqueda tiene dos pasos ([ADR-0010](decisions/0010-busqueda-en-dos-pasos.md)): un KNN sobre los vectores binarios de `vecb_N`, con los mismos filtros dentro, elige al menos 100 candidatos, y se reordenan con los vectores exactos de `vecx_N`. Con menos de 100 memorias que pasen los filtros, el resultado es exacto. Mismo número de candidatos finales.
4. **Fusión RRF** (solo en modo híbrido): `score(d) = Σ 1 / (60 + rank_i(d))`. Solo usa posiciones, así que no hace falta normalizar BM25 y coseno, que están en escalas incomparables. Se ordena y se corta en `k`. *(Hito 4)* En los empates tienen prioridad los vectores. No se desempata por id: los ULID tienen una parte aleatoria, y los resultados cambiaban entre ejecuciones.
5. **Modos:** *(Hito 4)* por defecto **solo vectores** ([ADR-0006](decisions/0006-busqueda-por-vectores.md)); `VIGENTE_SEARCH_MODE=hybrid|text` cambia el modo. Si alguna memoria no tiene vector (se guardó con el servicio caído, o es de otro modelo), la búsqueda por vectores no la vería, así que se combina con la de texto y se avisa hasta que se ejecute `reindex`. Comprobarlo cuesta 6 ms con 50.000 memorias. Sin `Embedder` configurado, el modo es `fts_only`. *(Hito 2)* Si el servicio de embeddings falla, también: la respuesta lleva un aviso en `warnings` y nada falla. Al guardar, la memoria se guarda sin vector y `vigente reindex` lo completa después.
6. **`include_history=true`** quita el filtro de vigencia y marca cada resultado con su `status`.

Cada resultado devuelve la memoria completa, `score`, `matched_by` (`["fts"]`, `["vector"]` o ambos) y `similarity` (coseno) si la encontró la búsqueda vectorial.

*(Hito 2)* Lo que midió el mini-benchmark ([resultados](../eval/results/embedding_choice.md), [ADR-0004](decisions/0004-modelo-de-embeddings.md)):

- **Solo vectores superó a la búsqueda híbrida** en ese conjunto (Recall@1 100% frente a 95% con embeddinggemma). Pero las preguntas se escribieron a propósito sin palabras en común con la respuesta y no incluyen coincidencias exactas (números, nombres raros, identificadores), que es donde BM25 aporta. Se mantuvo la fusión híbrida hasta decidirlo en el Hito 4 con datos más representativos. *(Hito 4)* Con E1 y LongMemEval, solo vectores ganó también, y pasó a ser el modo por defecto (ADR-0006).
- **No hay un umbral de similitud que separe** las preguntas con respuesta de las que no tienen. Por eso `min_recall_similarity` queda en 0 y la búsqueda siempre devuelve los vecinos más cercanos, aunque no tengan relación. Decidir que «no hay nada guardado sobre esto» le corresponde al agente.

## 7. Contratos MCP

Las fechas de entrada y salida van en ISO 8601. Los errores son estructurados (`code`, `message` y, si aplica, `current_id`).

| Herramienta | Entrada | Salida |
|---|---|---|
| Herramienta (anotación MCP) | Entrada | Salida |
|---|---|---|
| `remember` (no destructiva) | `content`, `kind="fact"`, `tags?`, `valid_from?`, `confidence?`, `from_outside?` | `memory`, `possible_supersedes: [{memory, matched_by, similarity}]`, `next_step`, `warnings` |
| `recall` (solo lectura) | `query`, `k=5` (máx. 50), `as_of?`, `include_history=false`, `kind?` | `mode`, `results: [...]`, `warnings` |
| `supersede` (no destructiva) | `old_id`; `also_old_ids?`; **uno** de `new_content` / `new_id`; `reason?`; `valid_from?` (solo con `new_content`); `from_outside?` | `new` (null si queda pendiente), `old` (con su `valid_to` cerrado), `also_old`, `warnings` |
| `forget` (**destructiva**) | `memory_id` | `deleted_ids: [...]`, `warnings`; error `protected` si es de una persona |
| `history` (solo lectura) | `memory_id` | `versions`, de la más antigua a la vigente |
| `list_memories` (solo lectura) | `kind?`, `status?`, `tag?`, `limit=20`, `cursor?` | `items`, `next_cursor` |

**Cómo se detectan los «posibles reemplazos» en `remember`:**

- Con embeddings: hasta 3 hechos vigentes del mismo workspace con similitud coseno ≥ umbral. *(Hito 2)* El umbral depende del modelo y se calibró en el mini-benchmark: 0.51 para embeddinggemma (ADR-0004). Con él, F1 subió de 0.43 (BM25) a 0.76 y ninguna frase sin relación recibió propuestas. Pero las frases *del mismo tema* que no reemplazan nada («Ana trabaja como diseñadora» frente a «Ana vive en Madrid») sí las reciben: la similitud no distingue «actualiza» de «añade». Eso confirma con datos el ADR-0003: decide el agente.
- Sin embeddings: top-3 por BM25. Es una señal más débil y se documenta como limitación. *(Hito 1)* Ejemplo real: «Migramos a SQLite para el MVP» **no** propone «El proyecto usa PostgreSQL», porque no comparten palabras. Es justo el caso que deben resolver los embeddings del Hito 2. Con BM25, `score` no es comparable entre consultas.

Las descripciones de las herramientas indican cuándo usarlas, por ejemplo: «si `remember` devuelve un posible reemplazo que contradice lo nuevo, llama a `supersede` en vez de dejar ambos». También avisan de que el contenido devuelto son **datos guardados**, no instrucciones.

**Implementación** *(Hito 3, [`mcp_server.py`](../src/vigente/mcp_server.py), SDK oficial `mcp` 2.x, transporte stdio)*:

- **Instrucciones del servidor:** explican el protocolo (buscar antes de responder, reemplazar en vez de duplicar, `forget` solo si el usuario lo pide) y advierten que el contenido de las memorias son datos, nunca órdenes. Es una defensa contra memorias envenenadas con instrucciones.
- **Vistas compactas:** el agente recibe solo los campos que le sirven (sin `workspace_id`, `source` ni el score de RRF), porque cada campo de la respuesta forma parte de su prompt.
- **Anotaciones MCP:** `readOnlyHint` en las de lectura, `destructiveHint` solo en `forget` (en MCP, una herramienta que no es de solo lectura se considera destructiva por defecto) y `openWorldHint=false` en todas. Así el cliente puede pedir confirmación solo donde hace falta.
- **Errores:** `ToolError` con el formato `código: mensaje`, más `(current_id: …)` cuando aplica. Los errores de validación dicen qué campo falló **sin repetir el valor enviado**, que puede ser el contenido de una memoria.
- **Una conexión por llamada:** el SDK ejecuta las herramientas síncronas en un pool de hilos, y una conexión SQLite no debe compartirse entre hilos. Abrir y cerrar el servicio (conexión, carga de sqlite-vec, pragmas y comprobación de migraciones) cuesta 1,8 ms de mediana y 2,0 ms en p95 (300 mediciones sobre una base con 200 memorias, Apple Silicon). Abrirlo para leer no toma el bloqueo de escritura: el workspace solo se crea si no existe. El cliente HTTP de embeddings sí se comparte.
- **stdout es el canal del protocolo:** nada escribe en stdout, y los logs del SDK van a stderr en nivel WARNING. A nivel INFO registraría los errores de validación con los valores de entrada, es decir, con contenido de memorias.
- *(Hito 6)* **Seguridad:** `remember` y `supersede` rechazan secretos (`secret_detected`) y caracteres invisibles que esconden texto (`hidden_characters`). Cada memoria devuelta lleva `flags` si parece una orden, tiene caracteres de ancho cero o contiene un secreto antiguo, y `recall` lo avisa en `warnings` (§11).
- **Por HTTP:** `vigente-mcp --http` sirve la misma memoria en `/mcp` (*streamable HTTP*), con un token obligatorio (`VIGENTE_TOKEN`, al menos 32 caracteres) que se compara en tiempo constante. Por defecto solo escucha en `127.0.0.1`. Detalle en el [ADR-0009](decisions/0009-compartir-por-http.md).
- **Arranque:** `vigente-mcp` valida la configuración (base de datos, workspace, URL de embeddings) antes de aceptar conexiones y, si algo falla, termina con un mensaje claro en stderr.

**Prueba con un agente real** *(Hito 3, [informe](../eval/results/agent_demo.md), `eval/agent_demo.py`)*. Cuatro sesiones independientes de Claude Code, que solo tienen las herramientas de Vigente. Ejecutada dos veces, con el mismo comportamiento las dos:

- El agente **busca antes de guardar**, aunque nadie se lo pida en el mensaje.
- Usa **los dos flujos de reemplazo** del ADR-0005. Cuando sabe qué cambia, hace `recall` y luego `supersede` con `new_content`. Cuando no, hace `remember`, recibe la propuesta y la enlaza con `supersede` con `new_id`.
- **Rellena `valid_from` con una fecha futura** cuando el usuario anuncia un cambio («desde la semana que viene»). El hecho anterior sigue vigente hasta esa fecha.
- **Escribe un `reason` útil**, y en una sesión posterior lo usa para explicar qué había antes.
- Hallazgo de la prueba: si Claude Code tiene disponible su memoria de archivos, el agente también la usa y **duplica lo guardado en otro sistema que no sabe de reemplazos**. Para medir Vigente de forma aislada, la demo quita las herramientas propias de Claude Code (`--tools ""`). Para un usuario real es un aviso que conviene poner en el README del hito 5.

## 8. Plan de evaluación

**Experimento central:** memoria ingenua (todo queda `active`) frente a memoria con reemplazos, con la misma recuperación. La tesis se sostiene si la tasa de resultados obsoletos baja y Recall@5 no empeora.

*(Hito 4)* **Ejecutado.** El protocolo definitivo, con las reglas fijadas antes de medir, está en [`eval/README.md`](../eval/README.md). Los resultados:

- **E1:** la tesis se sostiene. Con la memoria ingenua, el primer resultado es una versión obsoleta en el 51% de las preguntas con búsqueda por vectores, el 44% con la híbrida y el 33% solo con texto; con Vigente, en el 0%. Recall@5 no empeora, sino que sube a 100%.
- **E2 (LongMemEval):** sin reemplazos, en las preguntas de actualización la versión vieja sale antes que la nueva en torno a la mitad de las veces.
- **Decisiones:** búsqueda por vectores por defecto, y el umbral se mantiene en 0.51 ([decisiones](../eval/results/decisions.md)).
- **Escala:** [medida](../eval/results/scale.md) hasta 200.000 memorias.

Lo que sigue de esta sección es el plan original.

### E1. Sintético (en el repo, semilla fija, en español)

- **Datos:** entidades con atributos que cambian 1–4 veces (ciudad de una persona, base de datos de un proyecto, responsable de un módulo, preferencias), más distractores.
- **Respuesta correcta conocida:** para cada atributo se conoce su cadena de versiones.
- **Ingesta oráculo:** se usan los reemplazos verdaderos. Así se evalúa el mecanismo aislado de las decisiones del agente.
- **Preguntas:** sobre el estado actual y con `as_of` en fechas pasadas.
- **Métricas:** Recall@5, MRR, tasa de obsoletos en top-1 y top-5.

### E2. LongMemEval-cleaned (licencia MIT; se descarga con un script, no se incluye en el repo)

- **Formato confirmado:** cada pregunta trae `question_type`, las sesiones del historial con fechas (`haystack_dates`), `answer_session_ids` y `has_answer` en los turnos que contienen la evidencia. Las preguntas `_abs` (abstención) se excluyen de la evaluación de recuperación, como en el propio benchmark.
- **E2a, recuperación:** con `longmemeval_s_cleaned`, cada turno del usuario se ingiere como `episode` con su fecha. Se mide Recall@5/10 por sesión y por turno, y MRR, comparando FTS, vector e híbrido.
- **E2b, actualización de conocimiento (opcional, se decide en el Hito 4):** con las preguntas `knowledge-update` del archivo *oracle* y `qwen3:8b` en Ollama como agente simulado. El agente procesa las sesiones en orden y decide `remember` o `supersede`, y se mide la tasa de obsoletos de extremo a extremo. Es la única forma honesta de probar en LongMemEval la parte que depende del agente. Hay que verificar antes cómo distinguir la evidencia vieja de la nueva en esa categoría.

### Común a ambos

- **Latencia:** p50 y p95 de `recall` y de escritura, y tiempo de indexación, con el hardware y las versiones de los modelos anotados.
- **Salida:** tabla en Markdown y un gráfico, generados por `vigente eval`. Los resultados se publican tal como salgan.

## 9. Decisiones (aprobadas el 18-11-2026)

1. **`forget` borra la cadena completa**, no solo una versión.
2. **Los `episode` son inmutables** y no se pueden reemplazar.
3. **E2b (agente simulado con qwen3:8b)** queda como opcional para el Hito 4. *(Hito 4)* Se dejó fuera: llevaría horas de cómputo y mediría sobre todo al modelo local. El protocolo con un agente real ya está demostrado en la demo del Hito 3.
4. **Modelo de embeddings:** se compararon `qwen3-embedding:0.6b` y `embeddinggemma` con un mini-benchmark propio. Elegido: **embeddinggemma** ([ADR-0004](decisions/0004-modelo-de-embeddings.md)). *(Hito 2)*
5. **Nombre «vigente».**
6. **Git:** propongo `git init` al empezar el Hito 1 y un commit por hito, con tu autorización en cada uno.

## 10. Riesgos

| Riesgo | Mitigación |
|---|---|
| sqlite-vec es pre-1.0 (v0.1.9) | Versión fijada; tests de integración contra la extensión real |
| Filas fantasma tras un insert fallido | Transacción explícita en toda escritura; validación con Pydantic antes de llegar a SQL; `vigente doctor` |
| `NULL` no permitido en metadatos | Centinela 31-12-9999, convertido solo en `store.py` |
| Sintaxis FTS5 en consultas del usuario | Tokenizar y citar cada token |
| Mezcla de español e inglés | `unicode61` sin stemming; modelo de embeddings multilingüe |
| El agente puede no llamar a `supersede` | Descripciones claras de las herramientas; se mide en E2b; se documenta como limitación |
| KNN de sqlite-vec es exacto (fuerza bruta) | *(Hito 4)* Medido: crecía de forma lineal, 64 ms con 50.000 memorias. *(v1.2)* Búsqueda en dos pasos, binaria y luego exacta: 7 ms con 50.000 y 25 ms con 200.000, sin cambiar los resultados de E1 ni de E2. Sigue siendo lineal: con millones haría falta un índice aproximado (ADR-0010) |
| Memoria envenenada o secretos guardados *(Hito 6)* | Se rechazan los secretos y los caracteres invisibles al guardar; las órdenes se marcan al leer, `recall` avisa y `vigente review` las lista. Las reglas no paran a un atacante que las conozca (ADR-0007) |
| Varios clientes (CLI, Claude Code, Codex, Cursor) sobre el mismo archivo | WAL + `busy_timeout`. *(v1.0.1)* Al crear la base a la vez fallaba el 57% de las aperturas; el paso a WAL se reintenta y un test lo comprueba con cuatro procesos. Probado también con tres servidores MCP escribiendo a la vez |
| Servicio de embeddings caído *(Hito 2)* | La memoria se guarda sin vector, la búsqueda cae a solo texto con aviso y `vigente reindex` completa lo pendiente |
| Enviar memorias a un servicio remoto sin querer *(Hito 2)* | Toda URL no local se rechaza salvo con `VIGENTE_ALLOW_REMOTE=1`; la clave de API solo llega por entorno |
| Umbral de reemplazos frágil *(Hito 2)* | Calibrado con solo 18 casos y margen estrecho (ADR-0004). *(Hito 4)* Recalibrado con E1 y validado aparte: se mantiene 0.51, porque el umbral calibrado con plantillas no generalizaba |
| Rendimiento que solo se ve con muchos datos *(Hito 4)* | El benchmark de escala encontró una búsqueda de texto con coste cuadrático (§6); un test comprueba el plan de la consulta |
| Resultados de evaluación no reproducibles *(Hito 4)* | El desempate de RRF dependía de ids aleatorios; corregido, y las evaluaciones se ejecutaron dos veces con resultados idénticos |
| Vecinos sin relación en los resultados *(Hito 2)* | Ningún umbral los separa en los datos actuales; se documenta y lo decide el agente |

## 11. Seguridad de la memoria *(Hito 6)*

Qué se decidió y qué alternativas se descartaron está en el [ADR-0007](decisions/0007-seguridad-de-la-memoria.md). Aquí, cómo encaja:

- **[`safety.py`](../src/vigente/safety.py)** reúne las reglas: expresiones regulares y una medida de entropía, sin dependencias ni LLM.
- **Al guardar,** `service.remember` y `service.supersede` comprueban el contenido, la fuente, el motivo y las etiquetas antes de calcular el embedding. Los secretos y los caracteres *tag* o bidireccionales se rechazan. Solo la CLI pasa `allow_secrets`; el servidor MCP no tiene forma de hacerlo.
- **Al leer,** `Memory.flags` es un campo calculado, no una columna: cubre las memorias anteriores sin migrar la base, y las reglas pueden mejorar sin reescribir datos.
- **Coste medido:** unos 13 µs por memoria al guardar y unos 25 µs por memoria leída. `vigente review` recorre el workspace entero, así que con 50.000 memorias rondaría el segundo (estimación, no medida).
- **Evaluación:** E3 ([informe](../eval/results/e3_security.md)), con casos y reglas fijados antes de escribir los detectores, y una comprobación con los mensajes reales de LongMemEval que llevó a corregir la regla de tokens.
- E2 guarda los mensajes de LongMemEval con `allow_secrets`: mide la búsqueda, no los filtros, y así sus resultados no cambian.
- *(sin publicar)* **Quién puede cambiar un hecho** ([ADR-0011](decisions/0011-quien-puede-cambiar-un-hecho.md)). `memories.origin` dice de quién viene cada memoria y lo fija la interfaz: la CLI abre el servicio como persona y el servidor MCP, como agente. `pending_changes` guarda los cambios que esperan a una persona: no son memorias, así que ninguna búsqueda los ve, y su texto nunca se cita en un aviso. `contradictions` une dos memorias vigentes parecidas de distinto origen: `recall` no devuelve la menos fiable sin la otra.
- *(v1.3)* **Privacidad del archivo y de la red**, tras una auditoría del código:
  - la carpeta y el archivo de la memoria se crean con permisos solo para su dueño (0700 y 0600);
  - con un servicio de embeddings local se ignoran `HTTP_PROXY` y `ALL_PROXY`: si no, el texto de las memorias saldría hacia el proxy;
  - `reindex` comprueba que la memoria sigue siendo la misma antes de guardar su vector, para que el de un texto olvidado no acabe en otra memoria;
  - por HTTP, `--allowed-host` permite dejar el servidor en `127.0.0.1` con un proxy HTTPS delante ([ADR-0009](decisions/0009-compartir-por-http.md)).
