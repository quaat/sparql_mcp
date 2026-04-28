"""Strict Pydantic IR models for SPARQL query plans.

The IR is a typed AST. Validation and rendering operate on this tree; raw
SPARQL strings are produced only by the renderer at the very end.
"""

from graph_mcp.models import expressions as _expressions
from graph_mcp.models import patterns as _patterns
from graph_mcp.models.expressions import (
    AggregateExpr,
    BinaryExpr,
    BoundExpr,
    DateTimeExpr,
    ExistsExpr,
    Expression,
    FunctionExpr,
    InExpr,
    LangMatchesExpr,
    NotExistsExpr,
    NotExpr,
    RegexExpr,
    UnaryExpr,
)
from graph_mcp.models.iri import (
    Iri,
    LiteralValue,
    Prefix,
    PrefixedName,
    RdfTerm,
    Var,
)
from graph_mcp.models.literals import (
    DEFAULT_PREFIXES,
    LANG_TAG_REGEX,
    PREFIX_REGEX,
    VAR_NAME_REGEX,
)
from graph_mcp.models.patterns import (
    BindPattern,
    FilterPattern,
    GraphPattern,
    GroupPattern,
    MinusPattern,
    OptionalPattern,
    Pattern,
    PropertyPath,
    PropertyPathAlt,
    PropertyPathInverse,
    PropertyPathOneOrMore,
    PropertyPathSeq,
    PropertyPathTerm,
    PropertyPathZeroOrMore,
    PropertyPathZeroOrOne,
    ServicePattern,
    SubqueryPattern,
    TriplePattern,
    UnionPattern,
    ValuesPattern,
)
from graph_mcp.models.query_plan import (
    AskPlan,
    ConstructPlan,
    OrderClause,
    Projection,
    QueryPlan,
    SelectPlan,
)
from graph_mcp.models.results import (
    AskResult,
    BindingValue,
    ConstructResult,
    QueryExecutionMetadata,
    QueryResult,
    SelectResult,
    SolutionRow,
    Triple,
)
from graph_mcp.models.validation import ValidationIssue, ValidationResult

# --- Resolve cross-module forward references ------------------------------
# `NotExistsExpr` / `ExistsExpr` reference `Pattern` (defined in patterns.py).
# `SubqueryPattern` references `SelectPlan` (defined in query_plan.py).
# After all modules have loaded, rebuild the affected models with an explicit
# namespace that contains the missing names.

_ns: dict[str, object] = {
    "Pattern": Pattern,
    "_Pattern": Pattern,
    "SelectPlan": SelectPlan,
    "Expression": Expression,
}

_expressions.NotExistsExpr.model_rebuild(_types_namespace=_ns)
_expressions.ExistsExpr.model_rebuild(_types_namespace=_ns)
_patterns.SubqueryPattern.model_rebuild(_types_namespace=_ns)

# Rebuild every pattern type so the recursive `Pattern` discriminated union
# resolves cleanly across modules.
for _cls in (
    _patterns.GroupPattern,
    _patterns.OptionalPattern,
    _patterns.UnionPattern,
    _patterns.MinusPattern,
    _patterns.GraphPattern,
    _patterns.ServicePattern,
    _patterns.FilterPattern,
    _patterns.BindPattern,
    _patterns.TriplePattern,
):
    _cls.model_rebuild(_types_namespace=_ns)

SelectPlan.model_rebuild(_types_namespace=_ns)
AskPlan.model_rebuild(_types_namespace=_ns)
ConstructPlan.model_rebuild(_types_namespace=_ns)


__all__ = [
    "DEFAULT_PREFIXES",
    "LANG_TAG_REGEX",
    "PREFIX_REGEX",
    "VAR_NAME_REGEX",
    "AggregateExpr",
    "AskPlan",
    "AskResult",
    "BinaryExpr",
    "BindPattern",
    "BindingValue",
    "BoundExpr",
    "ConstructPlan",
    "ConstructResult",
    "DateTimeExpr",
    "ExistsExpr",
    "Expression",
    "FilterPattern",
    "FunctionExpr",
    "GraphPattern",
    "GroupPattern",
    "InExpr",
    "Iri",
    "LangMatchesExpr",
    "LiteralValue",
    "MinusPattern",
    "NotExistsExpr",
    "NotExpr",
    "OptionalPattern",
    "OrderClause",
    "Pattern",
    "Prefix",
    "PrefixedName",
    "Projection",
    "PropertyPath",
    "PropertyPathAlt",
    "PropertyPathInverse",
    "PropertyPathOneOrMore",
    "PropertyPathSeq",
    "PropertyPathTerm",
    "PropertyPathZeroOrMore",
    "PropertyPathZeroOrOne",
    "QueryExecutionMetadata",
    "QueryPlan",
    "QueryResult",
    "RdfTerm",
    "RegexExpr",
    "SelectPlan",
    "SelectResult",
    "ServicePattern",
    "SolutionRow",
    "SubqueryPattern",
    "Triple",
    "TriplePattern",
    "UnaryExpr",
    "UnionPattern",
    "ValidationIssue",
    "ValidationResult",
    "ValuesPattern",
    "Var",
]
