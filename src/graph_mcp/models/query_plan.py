"""Top-level QueryPlan: SELECT, ASK, CONSTRUCT."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from graph_mcp.models.expressions import Expression
from graph_mcp.models.iri import Prefix, Var
from graph_mcp.models.patterns import Pattern, TriplePattern


class _StrictBase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Projection(_StrictBase):
    """A single projection item.

    Either ``var`` (project variable as-is) or ``expression`` + ``alias``
    (project the value of ``expression`` AS ``alias``).
    """

    var: Var | None = None
    expression: Expression | None = None
    alias: Var | None = None

    def model_post_init(self, __context: object) -> None:
        has_var = self.var is not None
        has_expr = self.expression is not None
        if has_var == has_expr:
            raise ValueError("Projection must have exactly one of var or expression")
        if has_expr and self.alias is None:
            raise ValueError("Projection with expression requires an alias")
        if has_var and self.alias is not None:
            raise ValueError("Projection with var must not have an alias")

    @property
    def output_name(self) -> str:
        if self.alias is not None:
            return self.alias.name
        assert self.var is not None
        return self.var.name


class OrderClause(_StrictBase):
    expression: Expression
    descending: bool = False


class _BaseQuery(_StrictBase):
    """Common fields shared by all query forms."""

    prefixes: list[Prefix] = Field(default_factory=list)
    where: list[Pattern] = Field(default_factory=list)


class SelectPlan(_BaseQuery):
    kind: Literal["select"] = "select"
    distinct: bool = False
    reduced: bool = False
    projection: list[Projection] = Field(default_factory=list)
    """Empty projection means ``SELECT *``."""

    group_by: list[Var | Expression] = Field(default_factory=list)
    having: list[Expression] = Field(default_factory=list)
    order_by: list[OrderClause] = Field(default_factory=list)
    limit: int | None = None
    offset: int | None = None

    @field_validator("limit", "offset")
    @classmethod
    def _non_negative(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError("limit/offset must be non-negative")
        return v

    def model_post_init(self, __context: object) -> None:
        if self.distinct and self.reduced:
            raise ValueError("SELECT cannot be both DISTINCT and REDUCED")


class AskPlan(_BaseQuery):
    kind: Literal["ask"] = "ask"


class ConstructPlan(_BaseQuery):
    kind: Literal["construct"] = "construct"
    template: list[TriplePattern] = Field(default_factory=list)
    limit: int | None = None
    offset: int | None = None

    @field_validator("limit", "offset")
    @classmethod
    def _non_negative(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError("limit/offset must be non-negative")
        return v

    @field_validator("template")
    @classmethod
    def _non_empty_template(cls, v: list[TriplePattern]) -> list[TriplePattern]:
        if not v:
            raise ValueError("CONSTRUCT requires a non-empty template")
        return v


QueryPlan = Annotated[
    SelectPlan | AskPlan | ConstructPlan,
    Field(discriminator="kind"),
]

# Cross-module forward references are resolved in graph_mcp.models.__init__.
