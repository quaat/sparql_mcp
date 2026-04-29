"""Helpers that turn schema snapshots into ontology concepts.

The RAG vectorizer is not implemented yet, so the mock retriever needs to
build :class:`OntologyConcept` objects directly from the existing
:class:`graph_mcp.graph.schema_discovery.SchemaSnapshot`. Keeping this in a
small dedicated module makes it obvious which fields the future vectorizer
must populate so it can plug into the same retriever interface.
"""

from __future__ import annotations

from evals_rag.models import OntologyConcept
from graph_mcp.graph.schema_discovery import SchemaSnapshot


def concepts_from_snapshot(snapshot: SchemaSnapshot) -> list[OntologyConcept]:
    """Project a :class:`SchemaSnapshot` to a list of :class:`OntologyConcept`.

    The returned order is deterministic (classes, then properties, then
    individuals, then named graphs) so the mock retriever produces stable
    results across runs. ``source`` is set to ``"schema_snapshot"`` so a
    later vectorizer can distinguish auto-derived entries from curated ones.
    """
    out: list[OntologyConcept] = []
    for c in snapshot.classes:
        out.append(
            OntologyConcept(
                iri=c.iri,
                prefixed_name=c.prefixed_name,
                label=c.label,
                aliases=list(c.aliases),
                kind="class",
                description=c.description,
                source="schema_snapshot",
            )
        )
    for p in snapshot.properties:
        out.append(
            OntologyConcept(
                iri=p.iri,
                prefixed_name=p.prefixed_name,
                label=p.label,
                aliases=list(p.aliases),
                kind="property",
                description=p.description,
                domain=list(p.domain) + list(p.observed_domain),
                range=list(p.range) + list(p.observed_range),
                source="schema_snapshot",
            )
        )
    for ind in snapshot.individuals:
        out.append(
            OntologyConcept(
                iri=ind.iri,
                prefixed_name=ind.prefixed_name,
                label=ind.label,
                aliases=list(ind.aliases),
                kind="individual",
                description=ind.description,
                domain=list(ind.types),
                source="schema_snapshot",
            )
        )
    for g in snapshot.named_graphs:
        out.append(
            OntologyConcept(
                iri=g.iri,
                prefixed_name=g.prefixed_name,
                label=g.label,
                aliases=[],
                kind="graph",
                description=g.description,
                source="schema_snapshot",
            )
        )
    return out
