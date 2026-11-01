"""The boundary between AI interpretation and application logic."""

from __future__ import annotations

from typing import Protocol

from ..models import ParentRequest


class InterpretationError(RuntimeError):
    """The request could not be interpreted. The message is safe to show a parent."""


class Interpreter(Protocol):
    name: str

    def interpret(self, query: str) -> ParentRequest: ...
