# ADR-0008: Un hecho nuevo puede reemplazar a varios

- Estado: aceptado
- Fecha: 18-11-2026
- Amplía: [ADR-0002](0002-reemplazo-append-only-con-validez.md) y [ADR-0005](0005-dos-formas-de-reemplazar.md)

## Contexto

Las cadenas de versiones eran lineales: una versión nueva reemplazaba a un solo hecho. «Ana y Luis se mudaron juntos a Lisboa» no podía cerrar a la vez «Ana vive en Madrid» y «Luis vive en Valencia». El agente tenía que elegir uno, y el otro seguía vigente aunque ya fuera falso. Era una de las limitaciones del README.

## Decisión

- **`supersede` acepta `also_old_ids`:** los demás hechos que la versión nueva deja obsoletos. En la CLI es `--also`, que se puede repetir. Funciona con las dos formas del ADR-0005: crear la versión nueva (`new_content`) o enlazar una ya guardada (`new_id`).
- **Sin tabla nueva.** Cada hecho viejo ya apunta a su sucesor (`superseded_by_id`), así que varios pueden apuntar al mismo. El nuevo guarda en `supersedes_id` el reemplazado principal, y los demás se encuentran buscando quién apunta a él. La migración v3 añade el índice que hace rápida esa búsqueda.
- **Se pueden sumar hechos después** (v1.3). Si una versión ya reemplazó un hecho, `supersede(old_id=<otro>, new_id=<esa versión>)` cierra también el otro. Su hecho principal y su motivo no cambian.
- **Todo o nada.** Si alguno de los hechos ya estaba reemplazado o es un episodio, no se cierra ninguno. El error indica la versión vigente, como hasta ahora.
- **El historial recorre todas las versiones conectadas:** hacia delante cada versión tiene como mucho una sucesora, y hacia atrás puede haber varias ramas. Se ordena por vigencia.
- **`forget` borra todo lo conectado.** Si una versión unió los hechos de Ana y de Luis, olvidar cualquiera de ellos borra también los demás. Es lo más prudente para un borrado que existe por privacidad: la versión común contiene datos de los dos.

## Alternativas descartadas

- **Una tabla de enlaces entre versiones:** más general (también permitiría que un hecho se divida en varios), pero duplica una información que `superseded_by_id` ya tiene, y obliga a mantener las dos sincronizadas.
- **`forget` que borre solo la rama pedida:** dejaría la versión común sin uno de sus predecesores, y con un enlace roto.

## Consecuencias

- ⚠️ En la v1.2, la consulta que busca las versiones anteriores no usaba el índice de la migración v3: SQLite prefería otro y recorría el workspace entero en cada paso, así que el historial de un hecho con 10.000 versiones tardaba 7 s. La v1.3 obliga a usar ese índice (0,15 s) y quita el tope de 10.000 versiones, que impedía leer u olvidar un hecho tan largo aunque sí dejaba seguir alargándolo.

- ✅ Desaparece una limitación del README, sin cambiar las consultas de búsqueda: un hecho reemplazado sigue fuera de `recall` por sus fechas.
- ✅ Las bases existentes se migran solas: solo añade un índice.
- ⚠️ Un hecho no puede dividirse en varios («Ana y Luis vivían en Madrid» → cada uno en una ciudad). Se resuelve reemplazándolo por uno y guardando el otro como hecho nuevo.
