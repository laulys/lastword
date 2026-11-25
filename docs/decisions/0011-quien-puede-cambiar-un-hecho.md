# ADR-0011: Quién puede cambiar lo que es verdad

- Estado: aceptado
- Fecha: 25-11-2026
- Amplía: [ADR-0007](0007-seguridad-de-la-memoria.md) y [ADR-0008](0008-un-hecho-reemplaza-a-varios.md)

## Contexto

Los filtros del ADR-0007 buscan secretos, caracteres ocultos y órdenes. Un dato falso escrito como una frase normal no es ninguna de las tres cosas: «el proveedor cobra en la cuenta acabada en 9902» pasa. Y si llega por `supersede`, cierra el hecho verdadero: la mejor función de Lastword es también la mejor forma de atacarla.

[ShadowMerge](https://arxiv.org/abs/2605.09033) (Luo y otros, mayo de 2026) estudia este tipo de ataque en memorias en grafo: en vez de una orden, introduce una relación corriente que contradice a la verdadera. Sus autores miden un 93,8% de éxito medio sobre Mem0, concluyen que las defensas de entrada representativas no bastan, y señalan como líneas de defensa la procedencia, la autoridad de quien escribe y la resolución de conflictos. Lastword no es un grafo y no se ha ejecutado ShadowMerge contra él, pero la idea se traslada.

## Evidencia

**Antes de la protección (v1.3.1)**, con 12 hechos guardados por una persona y 12 versiones falsas guardadas por un agente:

- por `supersede`, se aceptan 12 de 12, sin ningún aviso, y `recall` devuelve solo el falso;
- por `remember`, se aceptan 12 de 12, sin ningún aviso, y `recall` devuelve los dos. Además, en los 12 `remember` propone al agente cerrar el hecho verdadero;
- el verdadero se recupera en 12 de 12, con `history` o con `as_of`. El daño se puede deshacer, pero nadie sabe que debe hacerlo.

## Qué hacen otros proyectos

Leído en su código el 25-11-2026, sin ejecutarlo. «No se encontró» no significa que no exista.

| Proyecto | Fechas de validez | ¿La confianza decide qué reemplaza a qué? |
|---|---|---|
| [Graphiti](https://github.com/getzep/graphiti/blob/38eef1eb9f9e993effc9801d64cb73aaf9d3cbbd/graphiti_core/utils/maintenance/edge_operations.py#L795) | Sí | No: entre dos datos que se contradicen vale el de fecha posterior. Su [política de seguridad](https://github.com/getzep/graphiti/blob/38eef1eb9f9e993effc9801d64cb73aaf9d3cbbd/SECURITY.md) dice que todos los episodios de un grupo tienen la misma confianza |
| [OWASP Agent Memory Guard](https://github.com/OWASP/www-project-agent-memory-guard/blob/2628cfdb91731ab80caf7fcb0adbe293645ce6e3/src/agent_memory_guard/policies/access.py#L58) | No se encontraron; hay una caducidad opcional en su almacén de Redis | En parte: reglas de quién escribe qué, y por defecto solo los administradores cambian políticas y preferencias verificadas. Tiene cuarentena y vuelta atrás |
| [ShieldCortex](https://github.com/Drakon-Systems-Ltd/ShieldCortex/blob/128a0734883f33309c9ebfe171c9511fcfb9f6b8/src/threat-graph/conflict.ts#L36) | En parte: inicio y fin en las relaciones de su grafo. No se encontró una consulta por fecha pasada | En parte: detecta el conflicto por la confianza de quien escribió, en cuatro tipos de relación; marca los dos datos y decide una persona |

La idea no es nueva. Lo que no se encontró junto en ninguno de los tres es aplicarla a frases corrientes, con fechas, y que el dato más fiable siga vigente mientras el otro espera. No se han revisado Mem0, Letta ni Zep para esto.

## Decisión

- **Cada memoria guarda su origen:** `person`, `agent` o `external` (contenido de fuera: un correo, una página).
- **El origen lo fija la interfaz, no quien escribe.** La CLI es `person`. Por MCP se es como mucho `agent`: el agente puede declarar que algo es `external`, y nunca subir de nivel.
- **Un origen menos fiable no cierra un hecho más fiable.** `supersede` guarda la versión nueva como **pendiente**: el hecho anterior sigue vigente, y `recall` lo devuelve con el aviso de que hay un cambio por confirmar.
- **Por `remember` también se comprueba.** Si el texto se parece a un hecho más fiable, se guarda, pero `recall` devuelve siempre los dos, el más fiable primero y con un aviso.
- **Enlazar vale lo que valga el menos fiable de los dos:** quien enlaza o la memoria enlazada. Así un agente no puede cerrar un hecho de una persona con otro hecho de esa persona que no tiene que ver. Tampoco puede enlazar un hecho suyo *hacia* el de una persona: escribiría en él su motivo y su historial.
- **Se acepta lo que se enseña.** `pending` muestra todo lo que se aplicaría (textos, fecha, etiquetas, motivo) y, si algo cambió después, la aceptación falla. Dos peticiones solo son la misma si coinciden en todo.
- **Olvidar también se protege.** Un agente no puede borrar un hecho que una persona guardó o confirmó: si no, bastaría borrar el verdadero y guardar el falso. Se hace en la CLI, que enseña todas las versiones antes de preguntar.
- **Lo que una persona acepta o enlaza pasa a ser de una persona,** para que el hecho no pierda la protección al cambiar.
- **El texto pendiente no sale en ninguna búsqueda ni aviso:** es lo que escribió quien ataca.
- **Decide una persona, en la CLI:** aceptar o rechazar. El servidor MCP no tiene esa herramienta.
- **Una persona puede confirmar un hecho** que guardó un agente, y entonces queda protegido.
- **Entre orígenes iguales no cambia nada.**

## Alternativas descartadas

- **Que gane el más reciente:** es lo que hay hoy, y es el ataque.
- **Marcar los dos como dudosos:** deja al agente sin respuesta justo cuando pregunta.
- **Que un LLM decida si el cambio es creíble:** el motor no llama a ninguno (ADR-0003), y una mentira bien escrita es creíble.
- **Que el agente pida confirmación:** es precisamente a quien se engaña.
- **Bloquear el cambio:** si es legítimo, se pierde.

## Límites conocidos

- ⚠️ **Protege lo que una persona guarda o confirma.** Si un agente guardó el hecho y otro agente lo cambia, tienen el mismo origen y el cambio se acepta. Sería la mayoría de las memorias en el uso normal, por eso existe la confirmación.
- ⚠️ **Un agente engañado puede no declarar que algo es externo.** Contaría como `agent`: basta para no tocar lo de una persona, no para proteger lo de otro agente.
- ⚠️ **Por HTTP, todos comparten un token** (ADR-0009). Para distinguir clientes haría falta un token por cliente.
- ⚠️ **Quien puede ejecutar la CLI es `person`**, también un agente con acceso a la terminal.
- ⚠️ **Lo guardado antes de la v7 no se compara al migrar.** Una mentira antigua no queda unida al hecho verdadero hasta que se confirma o se cambia: `lastword confirm <id>` lo comprueba.
- ⚠️ **Con una memoria salen como mucho 20 hechos más fiables,** los más parecidos, y un aviso con cuántos faltan. Una mentira escrita para parecerse a más de 20 podría dejar fuera el que contradice.
- ⚠️ **Solo se apuntan 1.000 memorias de agentes parecidas** al guardar un hecho de una persona, las más parecidas primero.
- ⚠️ **Un agente puede cambiar un hecho suyo que está en un cambio pendiente.** El cambio sale entonces como «ya no se puede aceptar» y hay que pedirlo otra vez.
- ⚠️ **Un agente puede llenar la cola de cambios pendientes** (200 por workspace). No cambia ningún hecho, pero hay que rechazarlos para que entren otros.

## Resultado (E5)

Protocolo fijado antes de implementar, en [eval/README.md](../../eval/README.md); [resultados completos](../../eval/results/e5_trust.md).

- **(a) Se cumple.** Ningún hecho de una persona queda cerrado ni borrado por `supersede`, `new_id`, reenlace o `forget`, con 12 pares inventados y 70 de LongMemEval, con y sin embeddings.
- **(b) Depende de cómo se lea.** El falso sale con el verdadero y con algún aviso en 63 de 69 pares de LongMemEval (91%), y en los 12 inventados. Si el aviso tiene que nombrar a los dos, que es lo que importa, son 58 de 69 (84%): no llega al 90%. La redacción de la regla no lo precisaba, y se vio al medir. El umbral no se ha ajustado con estos datos, y **esta vía no se da por protegida**.
- **(c) Se cumple.** Entre orígenes iguales, 326 de 326 cambios funcionan como antes.
- **(d) Se cumple.** El MRR de E1 y el de E2 no cambian.

**Se adopta** por (a), (c) y (d). El aviso de `remember` se queda como mitigación: nunca empeora lo que había, y con embeddings y k = 5 el falso no sale solo en ningún caso. Pero no es una garantía.

**Coste medido.** Con 50.000 hechos de agentes, guardar y buscar tardan lo mismo que en la v1.3.1 (medido a mano en la misma máquina: 7,2 y 6,9 ms, frente a 7,0 y 6,9). Una persona tarda más en guardar, unos 70 ms, porque se compara con todo lo de los agentes. Y un agente tarda entre 40 y 90 ms, según la carga, si hay 25.000 hechos de personas, que se miran todos para que no se pueda inundar.

## Vías que aparecieron al implementarlo

Esquivaban una versión más sencilla, y cada una tiene su test:

- contar la mentira como episodio;
- colarla reemplazando un hecho cualquiera de otro agente;
- guardar muchas copias para que el hecho verdadero no salga entre los parecidos;
- cambiar la mentira por otra, o esperar a que la persona actualice el hecho, para perder el aviso;
- guardarla antes que la verdad.

## Vías que encontró una revisión, antes de publicar

E5 mide cinco vías, y con todas cumplía. Una revisión de seguridad con el método de [security-audit](https://github.com/cloudflare/security-audit-skill) de Cloudflare (varios revisores automáticos que no ven las conclusiones de los demás, y dos pasadas más que intentan romper los arreglos) encontró estas otras. **Las dos primeras dejaban la protección sin efecto, y E5 no las veía: medir bien unas vías no demuestra que no haya otras.** Todas están corregidas y tienen su test, en [`test_trust.py`](../../tests/test_trust.py) o [`test_safety.py`](../../tests/test_safety.py):

- pedir que una memoria inofensiva reemplace al hecho y, mientras espera, cambiar esa memoria: la persona aceptaba un texto y regía otro;
- enlazar un hecho propio hacia el de la persona: escribía en él un motivo falso, y podía acabar borrado con el otro;
- códigos de terminal en el texto: `pending` enseñaba una cosa y se aplicaba otra (ver el [ADR-0007](0007-seguridad-de-la-memoria.md));
- un terminal estrecho, donde la tabla de `pending` recortaba por la derecha parte del texto que se aceptaba;
- añadir historial falso a la memoria propuesta después de enseñarla;
- una fecha o un motivo que `pending` no enseñaba, también colados en la petición idéntica de otro agente;
- confirmar un hecho que ya se había reemplazado: decía «confirmada» y no protegía nada;
- una mentira unida a otra mentira, en vez de al hecho: `recall` daba por buena la segunda;
- fechar la mentira en el futuro, adelantarse a un cambio ya anunciado, contarla como episodio antes que la verdad, o guardar una copia exacta y esperar a que el hecho cambiara;
- borrar a la vez una memoria parecida: los choques se apuntaban en una transacción aparte, que fallaba entera. Ahora se buscan y se apuntan en la que guarda la memoria;
- una mentira sobre el pasado, que solo se ve con `as_of`, o que cambia una mayúscula de un enlace;
- confirmar un hecho con un motivo escrito por un agente: el motivo pasaba a ser de la persona;
- rechazar un cambio borraba el choque ya apuntado. Este fallo lo metió uno de los arreglos, y lo vio la segunda pasada;
- borrar un hecho propio para retirar un cambio pendiente que también tocaba el de una persona, o aprovechar una carrera para dejarlo a medias.

## Migración

La v7 añade el origen: lo guardado desde la CLI pasa a ser de una persona y lo demás, de un agente. Es fiable porque el servidor MCP siempre escribió `source = 'mcp'`.
