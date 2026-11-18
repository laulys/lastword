"""Errores de dominio. Cada uno tiene un `code` estable que las interfaces exponen tal cual."""

from __future__ import annotations

from typing import ClassVar


class VigenteError(Exception):
    """Error esperado: la operación no se puede hacer tal como se pidió."""

    code: ClassVar[str] = "error"

    def __init__(self, message: str, *, current_id: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.current_id = current_id

    def describe(self) -> str:
        """`código: mensaje (current_id: …)`: formato de texto para agentes y logs."""
        text = f"{self.code}: {self.message}"
        return text if self.current_id is None else f"{text} (current_id: {self.current_id})"

    def to_dict(self) -> dict[str, str]:
        data = {"code": self.code, "message": self.message}
        if self.current_id is not None:
            data["current_id"] = self.current_id
        return data


class NotFoundError(VigenteError):
    code = "not_found"


class NotSupersedableError(VigenteError):
    code = "not_supersedable"


class AlreadySupersededError(VigenteError):
    code = "already_superseded"


class InvalidValidityError(VigenteError):
    code = "invalid_validity"


class SchemaVersionError(VigenteError):
    code = "schema_version"


class ConfigError(VigenteError):
    code = "config"


class DatabaseError(VigenteError):
    """SQLite no pudo abrir el archivo, o está ocupado por otro proceso."""

    code = "database"


class EmbeddingError(VigenteError):
    code = "embedding_unavailable"


class SecretDetectedError(VigenteError):
    """El texto parece contener una clave o contraseña (ADR-0007)."""

    code = "secret_detected"


class HiddenCharactersError(VigenteError):
    """El texto contiene caracteres invisibles que pueden esconder instrucciones (ADR-0007)."""

    code = "hidden_characters"
