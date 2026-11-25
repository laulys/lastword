# ADR-0003: El motor no llama a ningún LLM; propone y el agente decide

- Estado: aceptado
- Fecha: 18-11-2026

## Contexto

Detectar que «migramos a SQLite» reemplaza a «usamos PostgreSQL» requiere entender el lenguaje. Otros sistemas de memoria resuelven esto llamando a un LLM dentro del motor en cada escritura. Pero quien usa Lastword por MCP **ya es un LLM**.

## Decisión

- El motor no hace llamadas a LLMs en v1.
- Al guardar, `remember` devuelve los hechos vigentes más parecidos como «posibles reemplazos».
- El agente cliente decide si llama a `supersede`.
- Las descripciones de las herramientas MCP explican ese protocolo.

## Alternativas consideradas

- **LLM interno que extrae hechos y detecta contradicciones.** Es más automático, pero añade coste por escritura, latencia, dependencia de un proveedor y resultados no deterministas difíciles de testear. Además duplica un razonamiento que el agente ya está haciendo.
- **Reglas fijas (mismo sujeto + mismo atributo).** Exigen extraer sujeto y atributo, lo que nos devuelve al problema del LLM o a un esquema rígido que no encaja con texto libre.

## Consecuencias

- ✅ Motor determinista, sin coste por operación y testeable con un `FakeEmbedder`.
- ✅ Funciona sin red ni claves de API.
- ⚠️ La calidad depende de que el agente siga el protocolo. Es la principal limitación y se medirá: E2b del plan de evaluación usa `qwen3:8b` como agente simulado.
- ⚠️ Sin embeddings, la detección de posibles reemplazos usa BM25 y es más débil.
- 🔁 Puerta abierta: un juez LLM opcional (desactivado por defecto) podría añadirse después sin cambiar los contratos.
