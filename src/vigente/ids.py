"""Identificadores ULID: 48 bits de tiempo (ms) + 80 bits aleatorios, en base32 de Crockford.

Se ordenan por fecha de creación como texto y no necesitan dependencias.
"""

from __future__ import annotations

import os
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_MAX_TIMESTAMP_MS = 2**48


def new_ulid(timestamp_ms: int | None = None) -> str:
    ts = time.time_ns() // 1_000_000 if timestamp_ms is None else timestamp_ms
    if not 0 <= ts < _MAX_TIMESTAMP_MS:
        raise ValueError(f"timestamp fuera del rango de ULID: {ts}")
    value = (ts << 80) | int.from_bytes(os.urandom(10), "big")
    chars = []
    for _ in range(26):
        chars.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))
