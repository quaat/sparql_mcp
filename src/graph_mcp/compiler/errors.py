"""Structured compiler errors raised by validator/renderer."""

from __future__ import annotations

from graph_mcp.models.validation import ValidationResult


class CompilerError(Exception):
    """Base class for compiler-side failures."""


class ValidationError(CompilerError):
    """Raised when a plan fails validation. Carries the structured result."""

    def __init__(self, result: ValidationResult) -> None:
        self.result = result
        first = result.errors[0] if result.errors else None
        msg = first.message if first else "validation failed"
        super().__init__(msg)


class RenderError(CompilerError):
    """Raised when a plan cannot be rendered."""
