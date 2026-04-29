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
                NamedGraphTerm(
                    iri="http://example.org/g1",
                    prefixed_name="ex:g1",
                    label="primary graph",
                ),
                NamedGraphTerm(
                    iri="http://example.org/employmentGraph",
                    prefixed_name="ex:employmentGraph",
                ),
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


def test_resolve_named_graph_by_prefixed_name() -> None:
    """Graphs whose IRI is in the prefix table should resolve via ``prefix:local``."""
    r = TermResolver(_provider())
    res = r.resolve(["ex:employmentGraph"], expected_kinds=["graph"])
    top = res.candidates[0]
    assert top.iri == "http://example.org/employmentGraph"
    assert top.prefixed_name == "ex:employmentGraph"
    assert top.score == 1.0


def test_resolve_named_graph_by_local_camel() -> None:
    """``employment graph`` (camel-split local) should resolve to the named graph."""
    r = TermResolver(_provider())
    res = r.resolve(["employment graph"], expected_kinds=["graph"])
    top = res.candidates[0]
    assert top.iri == "http://example.org/employmentGraph"
    assert top.score == 1.0


def test_kind_filter() -> None:
    r = TermResolver(_provider())
    res = r.resolve(["Person"], expected_kinds=["property"])
    # No properties match "Person", so the response should be a single unknown.
    assert all(c.kind in ("property", "unknown") for c in res.candidates)


def test_resolves_people_to_person_class() -> None:
    """Plural "people" should normalize to the Person class label."""
    r = TermResolver(_provider())
    res = r.resolve(["people"], expected_kinds=["class"])
    top = res.candidates[0]
    assert top.kind == "class"
    assert top.iri == "http://example.org/Person"
    assert top.score == 1.0


def test_resolves_companies_to_company_class() -> None:
    """Regular plural "companies" → "company" should match the Company class."""
    r = TermResolver(_provider())
    res = r.resolve(["companies"], expected_kinds=["class"])
    top = res.candidates[0]
    assert top.kind == "class"
    assert top.iri == "http://example.org/Company"
    assert top.score == 1.0


def test_normalization_skips_latin_greek_endings() -> None:
    """Words ending in ``ss`` / ``us`` / ``is`` / ``os`` must not be stemmed."""
    from graph_mcp.graph.term_resolver import _normalize

    assert _normalize("class") == "class"
    assert _normalize("status") == "status"
    assert _normalize("analysis") == "analysis"
    assert _normalize("chaos") == "chaos"
