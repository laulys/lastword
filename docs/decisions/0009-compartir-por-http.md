# ADR-0009: La memoria se puede compartir entre ordenadores, por HTTP y con token

- Estado: aceptado
- Fecha: 18-11-2026

## Contexto

Vigente era una memoria local. Desde la v1.0.1, Claude Code, Codex y Cursor la comparten en el mismo ordenador, pero no llegaba a otros ordenadores, a compañeros de equipo ni a agentes en la nube. Era una de las limitaciones del README.

El protocolo MCP tiene un transporte HTTP (*streamable HTTP*), y el SDK oficial ya lo implementa. Lo que falta es decidir quién puede conectarse.

## Decisión

- **`vigente-mcp --http`** sirve la misma memoria en `/mcp`. Sin argumentos sigue funcionando por stdio, como hasta ahora.
- **El token es obligatorio.** Sin `VIGENTE_TOKEN`, de al menos 32 caracteres, no arranca. Cada petición tiene que llevar `Authorization: Bearer <token>` o recibe un 401. La comparación es en tiempo constante, para que el tiempo de respuesta no revele cuánto del token se acierta.
- **Por defecto solo escucha en el propio ordenador** (`127.0.0.1`), con la protección del SDK contra *DNS rebinding*, que solo acepta peticiones dirigidas a un nombre local.
- **Con un proxy HTTPS delante, `--allowed-host` declara su nombre público.** El proxy reenvía la petición con ese nombre en la cabecera `Host`, y sin declararlo el servidor la rechaza con un 421. Es la configuración recomendada: el puerto sin cifrar no sale del ordenador. Se añadió en la v1.3, después de que una auditoría viera que la v1.2 solo funcionaba detrás de un proxy escuchando en toda la red.
- **Para escuchar en la red hay que pedirlo** (`--host`), y entonces avisa de que el token viaja en claro si no hay HTTPS. Ahí la protección del SDK rechazaría a los propios clientes, así que se apaga, y protege el token.
- **El workspace lo sigue fijando el servidor:** quien se conecta comparte ese workspace y no puede leer otro.
- **Lo demás no cambia:** las mismas herramientas, las mismas instrucciones y los mismos filtros de seguridad (ADR-0007), que importan más cuando la memoria la comparten varias personas.

## Alternativas descartadas

- **OAuth**, que el SDK también admite: es lo adecuado para un servicio con muchos usuarios, pero exige un servidor de identidad. Para una memoria de equipo, un token compartido es suficiente y se configura en un minuto.
- **Sincronizar archivos** (copiar la base entre ordenadores): dos agentes escribiendo en copias distintas crearían conflictos. Un único servidor mantiene una sola fuente de verdad, con las transacciones de SQLite.
- **TLS dentro de Vigente:** un proxy (Caddy, nginx o un túnel) ya lo hace bien. Reimplementarlo añadiría código de seguridad delicado sin ganar nada.

## Consecuencias

- ✅ Varios ordenadores, un equipo o un agente en la nube pueden compartir una memoria. Lo prueba un test que arranca el servidor como proceso real y conecta dos clientes por HTTP.
- ✅ Configurarlo en Claude Code y en Codex es una línea (README).
- ⚠️ Todos los que tienen el token tienen los mismos permisos: no hay usuarios ni roles. Si un token se filtra, hay que cambiarlo en el servidor y en los clientes.
- ⚠️ Sin HTTPS delante, el token viaja en claro. El servidor lo avisa al arrancar fuera de `localhost`.
