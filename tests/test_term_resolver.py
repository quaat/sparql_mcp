"""Tests for the term resolver."""

from __future__ import annotations

from graph_mcp.graph.schema_discovery import (
    ClassTerm,
    NamedGraphTerm,
    PropertyTerm,
    SchemaSnapshot,
    StaticSchemaProvider,
)
from graph_mcp.graph.term_resolver import TermResolver


def _provider() -> StaticSchemaProvider:
    return StaticSchemaProvider(
        SchemaSnapshot(
            prefixes={"ex": "http://example.org/"},
            classes=[
                ClassTerm(
                    iri="http://example.org/Person",
                    prefixed_name="ex:Person",
                    label="Person",
                    aliases=["human"],
                ),
                ClassTerm(
                    iri="http://example.org/Company",
                    prefixed_name="ex:Company",
                    label="Company",
                ),
            ],
            properties=[
                PropertyTerm(
                    iri="http://example.org/worksFor",
                    prefixed_name="ex:worksFor",
                    label="works for",
                ),
                PropertyTerm(
                    iri="http://example.org/knows",
                    prefixed_name="ex:knows",
                    label="knows",
                ),
            ],
            named_graphs=[
                NamedGraphTerm(iri="http://example.org/g1", label="primary graph"),
            ],
        )
    )


def test_resolve_label_match() -> None:
    r = TermResolver(_provider())
    res = r.resolve(["Person"], expected_kinds=["class"])
    assert res.candidates[0].iri == "http://example.org/Person"
    assert res.candidates[0].score == 1.0


def test_resolve_alias_match() -> None:
    r = TermResolver(_provider())
    res = r.resolve(["human"], expected_kinds=["class"])
    assert res.candidates[0].iri == "http://example.org/Person"


def test_resolve_partial_match() -> None:
    r = TermResolver(_provider())
    res = r.resolve(["work"], expected_kinds=["property"])
    assert res.candidates[0].iri == "http://example.org/worksFor"


def test_resolve_unknown() -> None:
    r = TermResolver(_provider())
    res = r.resolve(["completely_made_up_xyz"], expected_kinds=["class"])
    assert res.candidates[0].kind == "unknown"


def test_resolve_named_graph() -> None:
    r = TermResolver(_provider())
    res = r.resolve(["primary"], expected_kinds=["graph"])
    assert res.candidates[0].iri == "http://example.org/g1"


def test_kind_filter() -> None:
    r = TermResolver(_provider())
    res = r.resolve(["Person"], expected_kinds=["property"])
    # No properties match "Person", so the response should be a single unknown.
    assert all(c.kind in ("property", "unknown") for c in res.candidates)
