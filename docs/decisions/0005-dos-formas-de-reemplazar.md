# ADR-0005: Reemplazar con texto nuevo o enlazando una memoria ya guardada

- Estado: aceptado
- Fecha: 18-11-2026

## Contexto

Al diseñar el protocolo del agente para el servidor MCP (hito 3) apareció un problema. `remember` guarda el hecho nuevo y propone posibles reemplazos, pero la única forma de reemplazar era `supersede(old_id, new_content)`, que **crea otra memoria**. El flujo natural de un agente era:

1. `remember("Migramos a SQLite")`, que se guarda y propone «El proyecto usa PostgreSQL».
2. `supersede(old_id=<PostgreSQL>, new_content="El proyecto usa SQLite")`.

Ese flujo dejaba el mismo hecho dos veces vigente: la memoria del paso 1 y la del paso 2.

## Decisión

`supersede` acepta **exactamente una** de dos formas de indicar la versión nueva:

- `new_content`: la crea ahora. Sirve cuando el agente ya sabía qué hecho cambiaba (lo encontró antes con `recall`).
- `new_id`: **enlaza** una memoria ya guardada, normalmente la que `remember` acaba de guardar y que propuso como reemplazo. No se duplica nada.

La respuesta de `remember` incluye un campo `next_step` con la llamada exacta (`supersede(old_id=…, new_id="…")`), para que el agente no tenga que deducir el protocolo.

Reglas del enlace, comprobadas dentro de la transacción:

- Las dos memorias son `fact`. La anterior sigue vigente y es la última versión de su cadena.
- La nueva no es ya la versión nueva de otro hecho, y el enlace no crea un ciclo.
- La nueva empieza después que la anterior. La anterior termina justo cuando empieza la nueva: `old.valid_to = new.valid_from`.

## Alternativas consideradas

- **Herramienta de comprobación previa (`check` antes de `remember`).** Obliga a hacer siempre dos llamadas, también cuando no hay nada que reemplazar, que es lo habitual. Más latencia y más tokens en cada escritura.
- **`remember(replaces=old_id)`.** Exige saber qué se reemplaza *antes* de guardar, que es justo lo que `remember` descubre.
- **Que `remember` reemplace sola por encima del umbral.** Contradice el ADR-0003 y los datos del ADR-0004: las frases del mismo tema que no reemplazan nada también superan el umbral (10 de 10 con embeddinggemma).

## Consecuencias

- ✅ Ningún hecho queda duplicado. El caso habitual cuesta una llamada (`remember`) y el reemplazo, dos.
- ✅ La CLI tiene el mismo flujo: `vigente supersede <id> --by <id>`.
- ⚠️ Una herramienta con dos modos. Se valida que llegue exactamente uno de los dos, y el error lo explica.
- ⚠️ Las cadenas son lineales: una versión nueva reemplaza a un solo hecho. «Ana y Luis se mudaron a Lisboa» no puede cerrar dos hechos a la vez. Queda documentado como limitación.
