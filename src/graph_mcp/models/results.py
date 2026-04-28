"""Typed query results returned by the executor."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from graph_mcp.models.iri import Iri, LiteralValue


class _StrictBase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class BindingValue(_StrictBase):
    """A single SPARQL binding value (the result-side analog of an RDF term)."""

    type: Literal["uri", "literal", "bnode"]
    value: str
    datatype: str | None = None
    lang: str | None = None


class SolutionRow(_StrictBase):
    """One row of a SELECT result. Variables not bound for this row are absent."""

    bindings: dict[str, BindingValue]


class QueryExecutionMetadata(_StrictBase):
    duration_ms: float
    row_count: int | None = None
    truncated: bool = False
    endpoint: str | None = None


class SelectResult(_StrictBase):
    kind: Literal["select"] = "select"
    variables: list[str]
    rows: list[SolutionRow]
    metadata: QueryExecutionMetadata


class AskResult(_StrictBase):
    kind: Literal["ask"] = "ask"
    boolean: bool
    metadata: QueryExecutionMetadata


class Triple(_StrictBase):
    subject: Iri | LiteralValue | str
    predicate: Iri | str
    object: Iri | LiteralValue | str


class ConstructResult(_StrictBase):
    kind: Literal["construct"] = "construct"
    triples: list[Triple]
    metadata: QueryExecutionMetadata


QueryResult = Annotated[
    SelectResult | AskResult | ConstructResult,
    Field(discriminator="kind"),
]
