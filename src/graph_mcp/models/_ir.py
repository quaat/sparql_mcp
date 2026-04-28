"""Mutually-recursive IR: expressions, patterns, and query plans.

These three layers depend on one another (Expression contains
NotExistsExpr/ExistsExpr which contain list[Pattern]; Pattern contains
FilterPattern which contains Expression and SubqueryPattern which contains
SelectPlan; SelectPlan contains list[Pattern]).

Putting them in **one** module is the cheapest reliable way to make the cycle
resolvable: every forward reference is a name in the same module's globals,
so Pydantic v2's automatic forward-ref resolution works without injecting
custom type namespaces or running a rebuild loop.

The public names are re-exported from ``expressions.py``, ``patterns.py``,
and ``query_plan.py`` so existing imports keep working.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from graph_mcp.models.iri import Iri, LiteralValue, Prefix, PrefixedName, RdfTerm, Var

# --- Allowlists -------------------------------------------------------------

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


# --- Expressions ------------------------------------------------------------


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
    """``FILTER NOT EXISTS { ... }``."""

    kind: Literal["not_exists"] = "not_exists"
    patterns: list[Pattern]


class ExistsExpr(_StrictBase):
    kind: Literal["exists"] = "exists"
    patterns: list[Pattern]


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


# --- Property paths ---------------------------------------------------------


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


# --- Patterns ---------------------------------------------------------------


class TriplePattern(_StrictBase):
    """A basic triple pattern. ``predicate`` may be an IRI or a property path.

    Subject and object cannot be property paths; the :data:`RdfTerm`
    discriminated union enforces that.
    """

    kind: Literal["triple"] = "triple"
    subject: RdfTerm
    predicate: Iri | PrefixedName | Var | PropertyPath = Field(...)
    object: RdfTerm


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


# --- Plans ------------------------------------------------------------------


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


# --- Resolve forward references ------------------------------------------
# Every forward-referenced name (Expression, Pattern, SelectPlan, PropertyPath)
# is defined in *this* module. We rebuild every model that contains a forward
# reference, passing the module globals explicitly. The explicit namespace
# avoids any heuristic name-resolution that has historically caused slow or
# intermittent imports on some Pydantic 2.x / Python 3.13 combinations.

_REBUILD_NAMESPACE: dict[str, object] = {
    "Expression": Expression,
    "Pattern": Pattern,
    "PropertyPath": PropertyPath,
    "QueryPlan": QueryPlan,
    "SelectPlan": SelectPlan,
    "AskPlan": AskPlan,
    "ConstructPlan": ConstructPlan,
    "Iri": Iri,
    "PrefixedName": PrefixedName,
    "Var": Var,
    "LiteralValue": LiteralValue,
    "RdfTerm": RdfTerm,
    "Prefix": Prefix,
    "Projection": Projection,
    "OrderClause": OrderClause,
    "BinaryExpr": BinaryExpr,
    "UnaryExpr": UnaryExpr,
    "NotExpr": NotExpr,
    "InExpr": InExpr,
    "FunctionExpr": FunctionExpr,
    "RegexExpr": RegexExpr,
    "BoundExpr": BoundExpr,
    "LangMatchesExpr": LangMatchesExpr,
    "NotExistsExpr": NotExistsExpr,
    "ExistsExpr": ExistsExpr,
    "AggregateExpr": AggregateExpr,
    "DateTimeExpr": DateTimeExpr,
    "PropertyPathTerm": PropertyPathTerm,
    "PropertyPathInverse": PropertyPathInverse,
    "PropertyPathSeq": PropertyPathSeq,
    "PropertyPathAlt": PropertyPathAlt,
    "PropertyPathZeroOrMore": PropertyPathZeroOrMore,
    "PropertyPathOneOrMore": PropertyPathOneOrMore,
    "PropertyPathZeroOrOne": PropertyPathZeroOrOne,
    "TriplePattern": TriplePattern,
    "GroupPattern": GroupPattern,
    "OptionalPattern": OptionalPattern,
    "UnionPattern": UnionPattern,
    "MinusPattern": MinusPattern,
    "FilterPattern": FilterPattern,
    "BindPattern": BindPattern,
    "GraphPattern": GraphPattern,
    "ServicePattern": ServicePattern,
    "SubqueryPattern": SubqueryPattern,
    "ValuesPattern": ValuesPattern,
}


def _rebuild_recursive_models() -> None:
    """Rebuild every model that contains a forward reference.

    Called exactly once at import time. Splitting this into a function
    isolates the rebuild work from module-level state for easier testing.
    """
    for _model in (
        BinaryExpr,
        UnaryExpr,
        NotExpr,
        InExpr,
        FunctionExpr,
        RegexExpr,
        LangMatchesExpr,
        NotExistsExpr,
        ExistsExpr,
        AggregateExpr,
        DateTimeExpr,
        PropertyPathInverse,
        PropertyPathSeq,
        PropertyPathAlt,
        PropertyPathZeroOrMore,
        PropertyPathOneOrMore,
        PropertyPathZeroOrOne,
        TriplePattern,
        GroupPattern,
        OptionalPattern,
        UnionPattern,
        MinusPattern,
        FilterPattern,
        BindPattern,
        GraphPattern,
        ServicePattern,
        SubqueryPattern,
        Projection,
        OrderClause,
        SelectPlan,
        AskPlan,
        ConstructPlan,
    ):
        _model.model_rebuild(_types_namespace=_REBUILD_NAMESPACE)


_rebuild_recursive_models()
