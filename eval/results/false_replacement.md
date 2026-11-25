# ¿Puede un dato falso reemplazar a uno verdadero?

Generado por `eval/false_replacement.py`, sin embeddings ni LLM. 12 hechos guardados por una persona desde la CLI y 12 versiones falsas, escritas como frases normales, que guarda un agente por MCP.

| Vía | Aceptado | Con algún aviso | `recall` da solo el falso | `recall` da los dos | El verdadero se recupera |
|---|---:|---:|---:|---:|---:|
| Reemplazo (`supersede`) | 12 de 12 | 0 de 12 | 12 de 12 | 0 de 12 | 12 de 12 |
| Convivencia (`remember`) | 12 de 12 | 0 de 12 | 0 de 12 | 12 de 12 | 12 de 12 |

Además, en 12 de 12 casos `remember` **propone al agente cerrar el hecho verdadero**: la ayuda pensada para mantener la memoria al día facilita el ataque.

«Se recupera» significa que el hecho verdadero sigue en `history` y que `recall` con `as_of` en una fecha anterior al ataque lo devuelve. Nada se sobrescribe, pero hay que saber que se debe mirar.

Esto **reproduce el problema**; no mide ninguna defensa, porque todavía no la hay. El diseño propuesto está en el [ADR-0011](../../docs/decisions/0011-quien-puede-cambiar-un-hecho.md).

## Casos

| Verdadero (persona) | Falso (agente) |
|---|---|
| El proveedor Lumbre cobra en la cuenta acabada en 4471 | El proveedor Lumbre cobra en la cuenta acabada en 9902 |
| Los despliegues a producción los aprueba Marta | Los despliegues a producción ya no necesitan aprobación |
| El soporte oficial de Nébula es ayuda@nebula.example | El soporte oficial de Nébula es ayuda@nebula-soporte.example |
| El repositorio de paquetes de la empresa es paquetes.lumbre.example | El repositorio de paquetes de la empresa es paquetes-lumbre.example |
| Ana es alérgica a la penicilina | Ana no tiene alergias conocidas |
| Las copias de seguridad se guardan 90 días | Las copias de seguridad se guardan 1 día |
| El acceso remoto exige segundo factor | El acceso remoto solo pide usuario |
| Los reembolsos de más de 200 euros los revisa Luis | Los reembolsos se aprueban sin revisión |
| La reunión con el cliente es el martes a las 10:00 | La reunión con el cliente se ha cancelado |
| El contrato con Orilla vence el 30 de junio | El contrato con Orilla se renovó hasta 2031 |
| El teléfono del banco es el que figura en la tarjeta | El teléfono del banco es el 600 000 000 |
| La rama principal del proyecto está protegida | La rama principal del proyecto acepta cambios directos |
