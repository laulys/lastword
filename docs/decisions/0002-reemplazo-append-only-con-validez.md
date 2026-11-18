# ADR-0002: Los hechos se reemplazan sin editarlos, con fechas de validez

- Estado: aceptado
- Fecha: 18-11-2026

## Contexto

La tesis del proyecto es que una memoria para agentes debe distinguir lo vigente de lo histórico. Hay que decidir qué pasa cuando un hecho cambia.

## Decisión

- Un hecho que cambia **no se edita**. `supersede` crea una memoria nueva, cierra el `valid_to` de la anterior y enlaza las dos (`supersedes_id` ↔ `superseded_by_id`).
- Se guardan dos tiempos:
  - `created_at` (tiempo de registro): cuándo lo supimos.
  - `valid_from` / `valid_to` (tiempo de validez): cuándo fue verdad.
- `as_of` consulta el tiempo de validez.
- Solo los `fact` se reemplazan. Los `episode` son inmutables.
- Reemplazar algo ya reemplazado da un error que incluye el id de la versión vigente. La cadena nunca se bifurca.

## Alternativas consideradas

- **Editar en el sitio (UPDATE del contenido).** Es lo más simple, pero pierde el historial y hace imposible `as_of`.
- **Tabla separada de versiones (`memory_versions`).** También da historial, pero obliga a decidir en cada consulta qué versión indexar y duplica lógica. Con la cadena enlazada, cada versión es una memoria normal con su propio texto, vector y validez, y la búsqueda no necesita casos especiales.
- **Un solo tiempo (`created_at`).** No permite registrar en mayo algo que cambió en marzo, que es el caso habitual en una conversación.

## Consecuencias

- ✅ `as_of` y `history` salen gratis de la misma estructura.
- ✅ Por defecto la búsqueda devuelve solo lo vigente, que es el comportamiento que medimos en la evaluación.
- ⚠️ La base crece con cada cambio. Es aceptable para memoria personal o de proyecto, y se medirá.
- ⚠️ `forget` tiene que recorrer la cadena completa para que el borrado sea real (ver arquitectura, §5).
