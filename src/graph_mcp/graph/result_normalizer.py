"""Normalize SPARQL JSON results into typed Pydantic models."""

from __future__ import annotations

from typing import Any

from graph_mcp.models import BindingValue, QueryExecutionMetadata, SelectResult, SolutionRow


def normalize_sparql_json(
    payload: dict[str, Any],
    metadata: QueryExecutionMetadata,
) -> SelectResult:
    """Convert a SPARQL 1.1 JSON results document into :class:`SelectResult`."""
    head = payload.get("head", {})
    variables = list(head.get("vars", []))
    results = payload.get("results", {})
    rows_raw = results.get("bindings", [])

    rows: list[SolutionRow] = []
    for row in rows_raw:
        bindings: dict[str, BindingValue] = {}
        for var, val in row.items():
            t = val.get("type")
            if t not in ("uri", "literal", "bnode", "typed-literal"):
                continue
            type_norm = "literal" if t == "typed-literal" else t
            bindings[var] = BindingValue(
                type=type_norm,  # type: ignore[arg-type]
                value=val.get("value", ""),
                datatype=val.get("datatype"),
                lang=val.get("xml:lang"),
            )
        rows.append(SolutionRow(bindings=bindings))

    return SelectResult(
        variables=variables,
        rows=rows,
        metadata=metadata.model_copy(update={"row_count": len(rows)}),
    )
