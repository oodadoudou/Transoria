"""Structured bridge errors.

The frontend branches on :attr:`BridgeError.code`; the human-readable
``message`` is fallback text for unrecognized codes and log lines. Codes
match the contract in ``docs/active/frontend-backend-bridge-contract.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class BridgeErrorPayload:
    """JSON-serializable error envelope sent across the bridge."""

    code: str
    message: str
    retryable: bool = False
    message_key: str | None = None
    details: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.message_key is not None:
            payload["message_key"] = self.message_key
        if self.details:
            payload["details"] = dict(self.details)
        return payload


class BridgeError(Exception):
    """Exception raised by bridge handlers.

    Handlers raise ``BridgeError`` for *expected* failures. The router
    serializes the exception into the contract's error envelope. Anything
    else (programmer errors) bubbles up as ``bridge.io_error`` so the UI can
    still render a useful message.
    """

    __slots__ = ("payload",)

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        message_key: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.payload = BridgeErrorPayload(
            code=code,
            message=message,
            retryable=retryable,
            message_key=message_key,
            details=dict(details) if details else {},
        )

    @property
    def code(self) -> str:
        return self.payload.code

    @property
    def retryable(self) -> bool:
        return self.payload.retryable

    @classmethod
    def invalid_argument(
        cls,
        message: str,
        *,
        field: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> "BridgeError":
        merged: dict[str, object] = dict(details or {})
        if field is not None:
            merged["field"] = field
        return cls("bridge.invalid_argument", message, details=merged)

    @classmethod
    def not_found(
        cls, message: str, *, details: Mapping[str, object] | None = None
    ) -> "BridgeError":
        return cls("bridge.not_found", message, details=details)

    @classmethod
    def conflict(
        cls, message: str, *, details: Mapping[str, object] | None = None
    ) -> "BridgeError":
        return cls("bridge.conflict", message, details=details)


__all__ = ["BridgeError", "BridgeErrorPayload"]
