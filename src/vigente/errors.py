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
