"""Graph pattern AST.

Includes: triples, groups, optional, union, minus, filter, bind, values,
named-graph, service, subquery, and property paths.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from graph_mcp.models.expressions import Expression
from graph_mcp.models.iri import Iri, LiteralValue, PrefixedName, RdfTerm, Var

if TYPE_CHECKING:
    from graph_mcp.models.query_plan import SelectPlan


class _StrictBase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


# --- Property paths --------------------------------------------------------


class PropertyPathTerm(_StrictBase):
    """Atomic predicate IRI in a property path. Optionally inverted with ``^``."""

    kind: Literal["term"] = "term"
    iri: Iri | PrefixedName
    inverse: bool = False


class PropertyPathInverse(_StrictBase):
    kind: Literal["inverse"] = "inverse"
    operand: PropertyPath


class PropertyPathSeq(_StrictBase):
    kind: Literal["seq"] = "seq"
    elements: list[PropertyPath]

    @field_validator("elements")
    @classmethod
    def _at_least_two(cls, v: list[object]) -> list[object]:
        if len(v) < 2:
            raise ValueError("seq path requires at least two elements")
        return v


class PropertyPathAlt(_StrictBase):
    kind: Literal["alt"] = "alt"
    elements: list[PropertyPath]

    @field_validator("elements")
    @classmethod
    def _at_least_two(cls, v: list[object]) -> list[object]:
        if len(v) < 2:
            raise ValueError("alt path requires at least two alternatives")
        return v


class PropertyPathZeroOrMore(_StrictBase):
    kind: Literal["zero_or_more"] = "zero_or_more"
    operand: PropertyPath


class PropertyPathOneOrMore(_StrictBase):
    kind: Literal["one_or_more"] = "one_or_more"
    operand: PropertyPath


class PropertyPathZeroOrOne(_StrictBase):
    kind: Literal["zero_or_one"] = "zero_or_one"
    operand: PropertyPath


PropertyPath = Annotated[
    (
        PropertyPathTerm
        | PropertyPathInverse
        | PropertyPathSeq
        | PropertyPathAlt
        | PropertyPathZeroOrMore
        | PropertyPathOneOrMore
        | PropertyPathZeroOrOne
    ),
    Field(discriminator="kind"),
]


# --- Triple patterns -------------------------------------------------------


class TriplePattern(_StrictBase):
    """A basic triple pattern. ``predicate`` may be an IRI or a property path.

    Subject and object cannot be property paths; the :data:`RdfTerm`
    discriminated union enforces that.
    """

    kind: Literal["triple"] = "triple"
    subject: RdfTerm
    predicate: Iri | PrefixedName | Var | PropertyPath = Field(...)
    object: RdfTerm


# --- Group / optional / union / minus / filter / bind / values / graph -----


class GroupPattern(_StrictBase):
    """A group ``{ ... }`` of patterns."""

    kind: Literal["group"] = "group"
    patterns: list[Pattern]


class OptionalPattern(_StrictBase):
    kind: Literal["optional"] = "optional"
    patterns: list[Pattern]


class UnionPattern(_StrictBase):
    kind: Literal["union"] = "union"
    branches: list[list[Pattern]]

    @field_validator("branches")
    @classmethod
    def _at_least_two(cls, v: list[list[object]]) -> list[list[object]]:
        if len(v) < 2:
            raise ValueError("union requires at least two branches")
        return v


class MinusPattern(_StrictBase):
    kind: Literal["minus"] = "minus"
    patterns: list[Pattern]


class FilterPattern(_StrictBase):
    kind: Literal["filter"] = "filter"
    expression: Expression


class BindPattern(_StrictBase):
    kind: Literal["bind"] = "bind"
    expression: Expression
    var: Var


class ValuesPattern(_StrictBase):
    """Inline values: ``VALUES (?a ?b) { (1 2) (3 4) }``."""

    kind: Literal["values"] = "values"
    variables: list[Var]
    rows: list[list[Iri | PrefixedName | LiteralValue | None]]

    @field_validator("variables")
    @classmethod
    def _non_empty_vars(cls, v: list[Var]) -> list[Var]:
        if not v:
            raise ValueError("VALUES requires at least one variable")
        names = [x.name for x in v]
        if len(set(names)) != len(names):
            raise ValueError("VALUES variables must be unique")
        return v

    def model_post_init(self, __context: object) -> None:
        for row in self.rows:
            if len(row) != len(self.variables):
                raise ValueError(
                    f"VALUES row arity {len(row)} does not match {len(self.variables)} variables"
                )


class GraphPattern(_StrictBase):
    """``GRAPH ?g { ... }`` — query a specific named graph."""

    kind: Literal["graph"] = "graph"
    graph: Var | Iri | PrefixedName
    patterns: list[Pattern]


class ServicePattern(_StrictBase):
    """``SERVICE <ep> { ... }``. Off by default; the validator gates it."""

    kind: Literal["service"] = "service"
    endpoint: Iri | PrefixedName
    silent: bool = False
    patterns: list[Pattern]


class SubqueryPattern(_StrictBase):
    """A nested SELECT subquery in pattern position."""

    kind: Literal["subquery"] = "subquery"
    select: SelectPlan


Pattern = Annotated[
    (
        TriplePattern
        | GroupPattern
        | OptionalPattern
        | UnionPattern
        | MinusPattern
        | FilterPattern
        | BindPattern
        | ValuesPattern
        | GraphPattern
        | ServicePattern
        | SubqueryPattern
    ),
    Field(discriminator="kind"),
]

# Forward references that span modules (Pattern in expressions.NotExistsExpr,
# SelectPlan in SubqueryPattern) are resolved by `graph_mcp.models.__init__`
# via a single coordinated `model_rebuild()` pass after all modules are loaded.
