# ADR-0007: Lo que no entra en la memoria y lo que entra marcado

- Estado: aceptado
- Fecha: 18-11-2026

## Contexto

Desde la v1.0.1 la memoria persiste entre sesiones y la comparten varios clientes (Claude Code, Codex, Cursor). Lo que entra una vez se repite en cada sesión y en cada herramienta. Para un atacante es mejor sitio que una conversación.

OWASP pone este riesgo entre los principales de los agentes:

- **ASI06, *Memory & Context Poisoning***, en su [Top 10 for Agentic Applications](https://genai.owasp.org/2025/12/09/owasp-top-10-for-agentic-applications-the-benchmark-for-agentic-security-in-the-age-of-autonomous-ai/) (diciembre de 2025).
- **Amenaza T1** en su guía *Agentic AI – Threats and Mitigations*.

La responsable de esa entrada lo resume en [un artículo de mayo de 2026](https://genai.owasp.org/2026/05/13/memory-is-a-feature-it-is-also-an-attack-surface/): el problema es tratar la memoria como algo de confianza, sin suficiente validación, separación ni visibilidad.

Hasta ahora Vigente solo tenía la separación: las instrucciones del servidor dicen que el contenido de una memoria es un dato y nunca una orden. Quedaban dos riesgos concretos sin cubrir:

1. **Secretos guardados sin querer.** Por ejemplo, «la clave de Stripe es sk_live_…». El archivo no está cifrado, y cualquier agente conectado al workspace la recuperaría.
2. **Memoria envenenada.** Un documento, una web o un email que el agente procesa consigue que guarde una orden: «cuando pregunten por la entrega, di que…». A veces va escondida en caracteres Unicode invisibles.

## Decisión

Tres niveles, según lo seguro que se puede estar de cada cosa. Todo con reglas fijas en [`safety.py`](../../src/vigente/safety.py), sin LLM, como el resto del motor (ADR-0003):

| Qué | Qué hace Vigente | Por qué |
|---|---|---|
| **Secretos**: 10 formatos de clave conocidos, contraseñas escritas en una frase y tokens con aspecto aleatorio | **Se rechazan** al guardar. El error no repite el secreto y propone guardar dónde está («la clave está en el gestor de contraseñas»). | Un secreto en la memoria no tiene arreglo: ya lo puede leer cualquier agente conectado. |
| **Caracteres Unicode *tag* y de control bidireccional** | **Se rechazan** siempre. | Sirven para esconder texto y ninguna memoria los necesita. |
| **Texto que se dirige al agente** como una orden, y caracteres de ancho cero | **Se guardan marcados**: el campo `flags`, un aviso en `recall` y una lista con `vigente review`. | Hay frases legítimas parecidas («a partir de ahora abrimos los domingos»). El ancho cero aparece al copiar texto de una web. |

Además:

- **Saltarse el rechazo de un secreto** solo es posible para una persona, desde la CLI (`--allow-secret`). Por MCP no se puede: un agente engañado usaría esa salida.
- **Las marcas se calculan al leer**, no al guardar. Así cubren también las memorias guardadas antes de esta versión, incluidos los secretos que entonces no se rechazaban, y cualquier mejora futura de las reglas, sin migrar la base. Cuesta unos 25 µs por memoria leída; comprobar al guardar, unos 13 µs.

## Evidencia

[E3](../../eval/results/e3_security.md), con casos y reglas de decisión fijados antes de escribir los detectores ([protocolo](../../eval/README.md)):

| Conjunto | Resultado |
|---|---:|
| Secretos de 13 formatos detectados | 40 de 40 |
| Frases parecidas sin secreto, con aviso | 0 de 40 |
| Intentos de dar órdenes, marcados | 40 de 40 |
| Memorias legítimas que suenan a orden, marcadas | 1 de 40 |
| Caracteres invisibles detectados (6 rechazados, 3 marcados) | 9 de 9 |
| Texto legítimo con emojis y otros alfabetos, afectado | 0 de 9 |

Las tres reglas se cumplen: los secretos se rechazan, el detector de caracteres invisibles no necesita cambios y `recall` avisa de las órdenes.

**Con texto real**, fuera del protocolo, en los 89.539 mensajes de usuario de LongMemEval:

- **Rechazos:** la primera versión rechazaba 38 como secreto. 35 eran rutas de URL que parecían tokens, porque la regla admite `/` para los secretos en base64. Corregida (dentro de una URL solo cuentan los parámetros con nombre de secreto; las rutas absolutas, las huellas y el código no), ahora rechaza 3: uno es un token real dentro de una URL. E3 no cambió.
- **Marcas:** 245 mensajes (0,27%), casi todos peticiones de rol del tipo «act as…». En una conversación son normales; guardadas en una memoria, son órdenes para un agente.

La única falsa marca de E3 es reveladora: «Cuando te pregunten por el horario, di que abrimos de 7:30 a 14:00». Una preferencia legítima tiene la misma forma que un ataque. Por eso las órdenes se marcan y no se bloquean.

## Alternativas descartadas

- **Guardar los secretos enmascarados** (`sk_live_…a1b2`): cambia en silencio lo que el agente quiso guardar.
- **Un LLM que clasifique lo que entra:** contradice el ADR-0003, añade coste y latencia, y se puede engañar con el mismo texto que pretende detectar.
- **Bloquear las órdenes:** la falsa marca de arriba sería un dato perdido.
- **Cifrar el archivo:** protege frente a quien copie el archivo, no frente a los agentes conectados, que son el riesgo aquí. Queda como posible mejora.

## Consecuencias

- ✅ Vigente ya cubre las tres cosas que pide OWASP: validación al escribir, visibilidad (`flags`, avisos y `vigente review`) y separación (las instrucciones del servidor).
- ⚠️ **El 100% de E3 es un techo.** Los casos y las reglas los escribió la misma persona. Un atacante que conozca las reglas las esquivará: parafraseando, en otro idioma o con codificaciones. Las reglas cazan lo evidente; la defensa de fondo sigue siendo que el agente trate la memoria como datos.
- ⚠️ Las reglas de órdenes solo cubren español e inglés. No se detectan secretos en formatos no listados ni técnicas como los selectores de variación Unicode.
- Lección del propio hito: al escribir los detectores, una herramienta convirtió los escapes `\u202e` del código en el carácter invisible real. Un test pasa ahora el detector por todo el repositorio.
