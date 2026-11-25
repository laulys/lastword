# ADR-0011: Quién puede cambiar lo que es verdad

- Estado: **propuesto, sin implementar**
- Fecha: 25-11-2026
- Amplía: [ADR-0007](0007-seguridad-de-la-memoria.md) y [ADR-0008](0008-un-hecho-reemplaza-a-varios.md)

## Contexto

Los filtros del ADR-0007 buscan secretos, caracteres ocultos y órdenes. Un dato falso escrito como una frase normal no es ninguna de las tres cosas: «el proveedor cobra en la cuenta acabada en 9902» pasa. Y si llega por `supersede`, cierra el hecho verdadero: la mejor función de Vigente es también la mejor forma de atacarla.

[ShadowMerge](https://arxiv.org/abs/2605.09033) (Luo y otros, mayo de 2026) estudia este tipo de ataque en memorias en grafo: en vez de una orden, introduce una relación corriente que contradice a la verdadera. Sus autores miden un 93,8% de éxito medio sobre Mem0, concluyen que las defensas de entrada representativas no bastan, y señalan como líneas de defensa la procedencia, la autoridad de quien escribe y la resolución de conflictos. Vigente no es un grafo y no se ha ejecutado ShadowMerge contra él, pero la idea se traslada.

## Evidencia

[`eval/false_replacement.py`](../../eval/false_replacement.py), con 12 hechos guardados por una persona y 12 versiones falsas guardadas por un agente ([resultados](../../eval/results/false_replacement.md)):

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

## Propuesta

- **Cada memoria guarda su origen:** `person`, `agent` o `external` (contenido de fuera: un correo, una página).
- **El origen lo fija la interfaz, no quien escribe.** La CLI es `person`. Por MCP se es como mucho `agent`: el agente puede declarar que algo es `external`, y nunca subir de nivel.
- **Un origen menos fiable no cierra un hecho más fiable.** `supersede` guarda la versión nueva como **pendiente**: el hecho anterior sigue vigente, y `recall` lo devuelve con el aviso de que hay un cambio por confirmar.
- **Por `remember` también se comprueba.** Si el texto se parece a un hecho de una persona, `recall` avisa de la posible contradicción.
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

## Cómo se medirá (E5)

Regla fijada antes de implementar, y publicada en un commit anterior:

- **Datos:** los 12 casos de la reproducción y otros 30 escritos por alguien que no haya escrito las reglas, o tomados de un conjunto público. Y 30 cambios legítimos.
- **Se mide, por cada par de orígenes y por las dos vías:** cuántos datos falsos llegan a ser la única respuesta de `recall`, cuántos llegan sin aviso y cuántos cambios legítimos quedan bloqueados.
- **Se adopta si:** (a) ningún dato de un origen menos fiable llega a ser la única respuesta frente a uno de una persona; (b) los cambios entre orígenes iguales funcionan como hoy en todos los casos; y (c) el MRR de E1 y el de E2 no bajan más de 0,005.
- **Se publica también lo que no protege:** el agente que cambia lo de otro agente.
