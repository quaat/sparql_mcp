"""Expression AST used in FILTER, BIND, HAVING, and aggregates."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from graph_mcp.models.iri import Iri, LiteralValue, PrefixedName, Var

if TYPE_CHECKING:
    from graph_mcp.models.patterns import Pattern as _Pattern

# Whitelist of safe SPARQL functions exposed via FunctionExpr.
ALLOWED_FUNCTIONS: frozenset[str] = frozenset(
    {
        "str",
        "lcase",
        "ucase",
        "strlen",
        "contains",
        "strstarts",
        "strends",
        "substr",
        "concat",
        "isiri",
        "isuri",
        "isblank",
        "isliteral",
        "isnumeric",
        "datatype",
        "lang",
        "abs",
        "ceil",
        "floor",
        "round",
        "if",
        "coalesce",
        "uri",
        "iri",
    }
)

ALLOWED_BINARY_OPS: frozenset[str] = frozenset(
    {"=", "!=", "<", "<=", ">", ">=", "+", "-", "*", "/", "&&", "||"}
)

ALLOWED_UNARY_OPS: frozenset[str] = frozenset({"-", "+"})

ALLOWED_AGGREGATES: frozenset[str] = frozenset(
    {"count", "sum", "avg", "min", "max", "sample", "group_concat"}
)

ALLOWED_DATETIME_ACCESSORS: frozenset[str] = frozenset(
    {"year", "month", "day", "hours", "minutes", "seconds", "now"}
)


class _StrictBase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class BinaryExpr(_StrictBase):
    kind: Literal["binary"] = "binary"
    op: str
    left: Expression
    right: Expression

    @field_validator("op")
    @classmethod
    def _check_op(cls, v: str) -> str:
        if v not in ALLOWED_BINARY_OPS:
            raise ValueError(f"unsupported binary operator: {v!r}")
        return v


class UnaryExpr(_StrictBase):
    kind: Literal["unary"] = "unary"
    op: str
    operand: Expression

    @field_validator("op")
    @classmethod
    def _check_op(cls, v: str) -> str:
        if v not in ALLOWED_UNARY_OPS:
            raise ValueError(f"unsupported unary operator: {v!r}")
        return v


class NotExpr(_StrictBase):
    kind: Literal["not"] = "not"
    operand: Expression


class InExpr(_StrictBase):
    kind: Literal["in"] = "in"
    operand: Expression
    options: list[Expression]
    negated: bool = False


class FunctionExpr(_StrictBase):
    kind: Literal["function"] = "function"
    name: str
    args: list[Expression]

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        if v.lower() not in ALLOWED_FUNCTIONS:
            raise ValueError(f"function not allowed: {v!r}")
        return v.lower()


class RegexExpr(_StrictBase):
    kind: Literal["regex"] = "regex"
    text: Expression
    pattern: str
    flags: str | None = None

    @field_validator("flags")
    @classmethod
    def _check_flags(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not all(c in "ismx" for c in v):
            raise ValueError(f"invalid regex flags: {v!r}")
        return v


class BoundExpr(_StrictBase):
    kind: Literal["bound"] = "bound"
    var: Var


class LangMatchesExpr(_StrictBase):
    kind: Literal["lang_matches"] = "lang_matches"
    tag: Expression
    range: Expression


class NotExistsExpr(_StrictBase):
    """``FILTER NOT EXISTS { ... }``. Patterns are typed via forward reference."""

    kind: Literal["not_exists"] = "not_exists"
    patterns: list[_Pattern]


class ExistsExpr(_StrictBase):
    kind: Literal["exists"] = "exists"
    patterns: list[_Pattern]


class AggregateExpr(_StrictBase):
    kind: Literal["aggregate"] = "aggregate"
    function: str
    expression: Expression | None = None  # None means COUNT(*)
    distinct: bool = False
    separator: str | None = None  # only valid for group_concat

    @field_validator("function")
    @classmethod
    def _check_func(cls, v: str) -> str:
        if v.lower() not in ALLOWED_AGGREGATES:
            raise ValueError(f"unsupported aggregate: {v!r}")
        return v.lower()

    def model_post_init(self, __context: object) -> None:
        if self.separator is not None and self.function != "group_concat":
            raise ValueError("separator is only valid for group_concat")
        if self.function != "count" and self.expression is None:
            raise ValueError(f"aggregate {self.function} requires an expression")


class DateTimeExpr(_StrictBase):
    """Date/time accessor: ``year(?d)``, ``now()``, etc."""

    kind: Literal["datetime"] = "datetime"
    accessor: str
    operand: Expression | None = None

    @field_validator("accessor")
    @classmethod
    def _check(cls, v: str) -> str:
        if v.lower() not in ALLOWED_DATETIME_ACCESSORS:
            raise ValueError(f"unsupported datetime accessor: {v!r}")
        return v.lower()

    def model_post_init(self, __context: object) -> None:
        if self.accessor == "now":
            if self.operand is not None:
                raise ValueError("now() takes no operand")
        elif self.operand is None:
            raise ValueError(f"{self.accessor} requires an operand")


# The discriminated union over all expression nodes.
Expression = Annotated[
    (
        Var
        | Iri
        | PrefixedName
        | LiteralValue
        | BinaryExpr
        | UnaryExpr
        | NotExpr
        | InExpr
        | FunctionExpr
        | RegexExpr
        | BoundExpr
        | LangMatchesExpr
        | NotExistsExpr
        | ExistsExpr
        | AggregateExpr
        | DateTimeExpr
    ),
    Field(discriminator="kind"),
]
