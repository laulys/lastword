"""Errores de dominio. Cada uno tiene un `code` estable que las interfaces exponen tal cual."""

from __future__ import annotations

from typing import ClassVar


class LastwordError(Exception):
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


class NotFoundError(LastwordError):
    code = "not_found"


class NotSupersedableError(LastwordError):
    code = "not_supersedable"


class AlreadySupersededError(LastwordError):
    code = "already_superseded"


class PendingConfirmationError(LastwordError):
    """El cambio quedó pendiente de que lo confirme una persona (ADR-0011)."""

    code = "pending_confirmation"


class ProtectedError(LastwordError):
    """Lo guardó o confirmó una persona, y solo una persona puede hacerlo (ADR-0011)."""

    code = "protected"


class InvalidValidityError(LastwordError):
    code = "invalid_validity"


class SchemaVersionError(LastwordError):
    code = "schema_version"


class ConfigError(LastwordError):
    code = "config"


class DatabaseError(LastwordError):
    """SQLite no pudo abrir el archivo, o está ocupado por otro proceso."""

    code = "database"


class EmbeddingError(LastwordError):
    code = "embedding_unavailable"


class SecretDetectedError(LastwordError):
    """El texto parece contener una clave o contraseña (ADR-0007)."""

    code = "secret_detected"


class HiddenCharactersError(LastwordError):
    """El texto contiene caracteres invisibles que pueden esconder instrucciones (ADR-0007)."""

    code = "hidden_characters"
