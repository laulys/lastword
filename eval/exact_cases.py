"""Casos de E4: búsquedas que dependen de un código o un identificador exacto.

Cada grupo tiene memorias casi iguales que solo cambian en el código («el error E-4031…»
frente a «el error E-4013…»), así que acertar exige distinguir el código, no el tema. Se
escribieron antes de medir y antes de implementar el modo automático (eval/README.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Code:
    code: str
    fact: str  # la memoria
    question: str  # pregunta que nombra el código


# Por grupo: códigos parecidos entre sí, con hechos distintos.
GROUPS: dict[str, list[Code]] = {
    "errores": [
        Code(
            "E-4031",
            "El error E-4031 aparece al pagar con tarjeta en la tienda",
            "¿Cuándo sale el error E-4031?",
        ),
        Code(
            "E-4013",
            "El error E-4013 aparece al iniciar sesión con Google",
            "¿Qué provoca el error E-4013?",
        ),
        Code(
            "E-4301",
            "El error E-4301 aparece al subir fotos de más de 10 MB",
            "¿En qué caso sale el E-4301?",
        ),
        Code(
            "E-3041",
            "El error E-3041 aparece al cambiar la dirección de envío",
            "Error E-3041: ¿cuándo pasa?",
        ),
        Code(
            "E-4310",
            "El error E-4310 aparece al aplicar un cupón caducado",
            "¿Por qué sale el error E-4310?",
        ),
        Code(
            "E-1034",
            "El error E-1034 aparece al exportar los pedidos a CSV",
            "¿Qué hace saltar el E-1034?",
        ),
    ],
    "pedidos": [
        Code(
            "7781",
            "El pedido 7781 es una tarta de queso para el sábado",
            "¿Qué lleva el pedido 7781?",
        ),
        Code(
            "7718",
            "El pedido 7718 son dos docenas de cruasanes para una oficina",
            "¿Qué es el pedido 7718?",
        ),
        Code(
            "8771",
            "El pedido 8771 es un roscón grande sin frutas",
            "Pedido 8771: ¿qué hay que preparar?",
        ),
        Code(
            "1778",
            "El pedido 1778 es pan sin gluten para un colegio",
            "¿Para quién es el pedido 1778?",
        ),
        Code(
            "7178",
            "El pedido 7178 es una tarta de cumpleaños con velas",
            "¿Qué incluye el pedido 7178?",
        ),
        Code("7871", "El pedido 7871 son empanadas para una boda", "¿De qué es el pedido 7871?"),
    ],
    "facturas": [
        Code(
            "F-2026-0117",
            "La factura F-2026-0117 está pagada por transferencia",
            "¿Cómo se pagó la F-2026-0117?",
        ),
        Code(
            "F-2026-0171",
            "La factura F-2026-0171 está pendiente desde octubre",
            "¿Está pagada la factura F-2026-0171?",
        ),
        Code(
            "F-2026-0711",
            "La factura F-2026-0711 se anuló por un error de IVA",
            "¿Qué pasó con la F-2026-0711?",
        ),
        Code(
            "F-2025-0117",
            "La factura F-2025-0117 se pagó con tarjeta en enero",
            "¿Cómo se pagó la factura F-2025-0117?",
        ),
        Code(
            "F-2026-1017",
            "La factura F-2026-1017 la reclamó el proveedor de harina",
            "¿Quién reclamó la F-2026-1017?",
        ),
    ],
    "incidencias": [
        Code(
            "SHOP-212",
            "La incidencia SHOP-212 es que el carrito se vacía al recargar",
            "¿De qué va SHOP-212?",
        ),
        Code(
            "SHOP-221",
            "La incidencia SHOP-221 es que no llegan los emails de pedido",
            "¿Qué problema es SHOP-221?",
        ),
        Code(
            "SHOP-122",
            "La incidencia SHOP-122 es que el buscador no encuentra tildes",
            "SHOP-122: ¿qué falla?",
        ),
        Code(
            "WEB-212",
            "La incidencia WEB-212 es que la portada tarda en cargar",
            "¿Qué es la incidencia WEB-212?",
        ),
        Code(
            "SHOP-2120",
            "La incidencia SHOP-2120 es que el horario sale en inglés",
            "¿Qué describe SHOP-2120?",
        ),
    ],
    "versiones": [
        Code(
            "2.4.1",
            "La versión 2.4.1 del tema arregla el menú en el móvil",
            "¿Qué arregla la versión 2.4.1?",
        ),
        Code("2.1.4", "La versión 2.1.4 del tema añade el modo oscuro", "¿Qué trae la 2.1.4?"),
        Code(
            "2.4.0",
            "La versión 2.4.0 del tema rompió los botones de pago",
            "¿Qué falló en la 2.4.0?",
        ),
        Code(
            "4.2.1",
            "La versión 4.2.1 de la app de pedidos cambia el icono",
            "¿Qué cambia en la versión 4.2.1?",
        ),
        Code(
            "1.4.2",
            "La versión 1.4.2 del tema quitó el carrusel de fotos",
            "¿Qué quitó la versión 1.4.2?",
        ),
    ],
    "productos": [
        Code(
            "TQ-GR-01",
            "El producto TQ-GR-01 es la tarta de queso grande",
            "¿Qué producto es el TQ-GR-01?",
        ),
        Code(
            "TQ-GR-10",
            "El producto TQ-GR-10 es la tarta de queso con arándanos",
            "¿Qué es TQ-GR-10?",
        ),
        Code(
            "TQ-PQ-01",
            "El producto TQ-PQ-01 es la tarta de queso pequeña",
            "¿A qué corresponde TQ-PQ-01?",
        ),
        Code(
            "TZ-GR-01",
            "El producto TZ-GR-01 es la tarta de zanahoria grande",
            "¿Qué es el producto TZ-GR-01?",
        ),
        Code(
            "TQ-GR-11",
            "El producto TQ-GR-11 es la tarta de queso sin lactosa",
            "¿Qué tarta es la TQ-GR-11?",
        ),
    ],
    "envíos": [
        Code(
            "1Z999AA10123456784",
            "El envío 1Z999AA10123456784 va a Bilbao con los moldes",
            "¿Adónde va el envío 1Z999AA10123456784?",
        ),
        Code(
            "1Z999AA10123456748",
            "El envío 1Z999AA10123456748 va a Sevilla con la harina",
            "¿Qué lleva el envío 1Z999AA10123456748?",
        ),
        Code(
            "1Z999AA10123465784",
            "El envío 1Z999AA10123465784 se devolvió por dirección incorrecta",
            "¿Qué pasó con el envío 1Z999AA10123465784?",
        ),
        Code(
            "1Z999AA10132456784",
            "El envío 1Z999AA10132456784 está retenido en aduanas",
            "¿Dónde está el 1Z999AA10132456784?",
        ),
    ],
}

# Memorias del mismo negocio sin códigos, para que la búsqueda no sea trivial.
DISTRACTORS: list[str] = [
    "La tienda online abre las 24 horas, pero los pedidos se preparan por la mañana",
    "Marta revisa los pagos pendientes cada lunes",
    "Los pedidos de tartas necesitan dos días de antelación",
    "El tema de la web se actualiza el primer martes de cada mes",
    "Las facturas se envían por email al cliente",
    "Los envíos a Canarias pagan aduanas",
    "El buscador de la tienda usa el nombre y la descripción del producto",
    "Los cupones de descuento caducan a los treinta días",
    "Las fotos de producto deben pesar menos de 10 MB",
    "El soporte responde las incidencias en menos de 24 horas",
    "La tarta de queso es el producto más vendido",
    "Los cruasanes se hornean a las seis de la mañana",
    "Los pedidos para colegios llevan etiqueta de alérgenos",
    "El inicio de sesión con Google se añadió en septiembre",
    "La exportación de pedidos sirve para la contabilidad",
]


def cases() -> list[tuple[str, Code]]:
    """(grupo, código) en orden fijo."""
    return [(group, code) for group, codes in GROUPS.items() for code in codes]


_PUNCTUATION = "¿?¡!,;:()[]{}«»\"'."
_YEAR = re.compile(r"(?:19|20)\d\d")
_DATE = re.compile(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4}")


def has_codes(text: str) -> bool:
    """¿Nombra la consulta un código? Una palabra de al menos 3 caracteres con alguna cifra,
    salvo años y fechas: E-4031, 7781, F-2026-0117, 2.4.1. Es la condición del modo
    automático que se evaluó en E4 (y en E1 y E2), y que no se adoptó."""
    for word in text.split():
        token = word.strip(_PUNCTUATION)
        if len(token) < 3 or not any(char.isdigit() for char in token):
            continue
        if not (_YEAR.fullmatch(token) or _DATE.fullmatch(token)):
            return True
    return False
