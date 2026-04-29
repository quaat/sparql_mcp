"""Tests for the deterministic mention extractor (§3)."""

from __future__ import annotations

from evals.mention_extractor import extract_mentions
from graph_mcp.graph.schema_discovery import (
    ClassTerm,
    IndividualTerm,
    PropertyTerm,
    SchemaSnapshot,
)


def _snap() -> SchemaSnapshot:
    return SchemaSnapshot(
        prefixes={"ex": "http://example.org/"},
        classes=[
            ClassTerm(iri="http://example.org/Person", prefixed_name="ex:Person", label="Person"),
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
            PropertyTerm(
                iri="http://example.org/age",
                prefixed_name="ex:age",
                label="age",
            ),
        ],
        individuals=[
            IndividualTerm(iri="http://example.org/alice", prefixed_name="ex:alice", label="Alice"),
            IndividualTerm(iri="http://example.org/Acme", prefixed_name="ex:Acme", label="Acme"),
        ],
    )


def _texts(question: str) -> set[str]:
    return {m.text.lower() for m in extract_mentions(question, _snap())}


def test_mentions_extracted_for_who_works_for_acme() -> None:
    texts = _texts("Who works for Acme?")
    # Schema-anchored: 'works for' (label of ex:worksFor) and 'Acme' (label of ex:Acme).
    assert any(t == "works for" or t == "worksfor" for t in texts)
    assert "acme" in texts


def test_mentions_pick_up_class_noun() -> None:
    texts = _texts("List all people in the company.")
    assert "people" in texts
    assert "company" in texts


def test_mentions_skip_imperative_stopwords() -> None:
    texts = _texts("Show every Person.")
    assert "show" not in texts
    # 'Person' is a schema label, so it's picked up via the label scan.
    assert "person" in texts


def test_mentions_pick_up_lowercase_individual_names() -> None:
    texts = _texts("Find what alice knows.")
    assert "alice" in texts
    assert "knows" in texts


def test_unresolved_capitalized_token_kept_as_individual_or_class() -> None:
    mentions = extract_mentions("Show me information about Aurora.", _snap())
    aurora = next(m for m in mentions if m.text == "Aurora")
    assert "individual" in aurora.expected_kinds
    assert "class" in aurora.expected_kinds
