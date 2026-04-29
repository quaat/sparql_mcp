"""Smoke tests for the import surface.

The MCP server consumes the ontology vectorizer through its public facade
only. This file pins that contract: importing :mod:`ontology_vectorizer`
and the names we depend on must work without a custom ``sys.path`` and
without dragging in MCP-internal modules.
"""

from __future__ import annotations

import sys


def test_vectorizer_top_level_import_works() -> None:
    import ontology_vectorizer  # noqa: F401  - the import IS the test


def test_public_api_names_resolve() -> None:
    from ontology_vectorizer import (
        OntologyConceptRetriever,
        OntologyConceptSearchRequest,
        OntologyConceptSearchResponse,
        OntologyConceptSearchResult,
        OntologyRetrievalError,
        OntologyRetrieverConfigError,
        OntologyVectorizerConfig,
        OntologyVectorizerError,
        config_from_env,
    )

    # Every name should be a real callable / class — no lazy stubs.
    assert callable(OntologyConceptRetriever)
    assert callable(OntologyConceptSearchRequest)
    assert callable(OntologyConceptSearchResponse)
    assert callable(OntologyConceptSearchResult)
    assert issubclass(OntologyRetrievalError, OntologyVectorizerError)
    assert issubclass(OntologyRetrieverConfigError, OntologyVectorizerError)
    assert callable(OntologyVectorizerConfig)
    assert callable(config_from_env)


def test_mcp_concept_retrieval_module_imports() -> None:
    from graph_mcp.concept_retrieval import (
        DiscoverOntologyConceptsInput,
        DiscoverOntologyConceptsOutput,
        MCPConceptRetrievalSettings,
        get_ontology_retriever,
        tool_discover_ontology_concepts,
    )

    assert callable(DiscoverOntologyConceptsInput)
    assert callable(DiscoverOntologyConceptsOutput)
    assert callable(MCPConceptRetrievalSettings)
    assert callable(get_ontology_retriever)
    assert callable(tool_discover_ontology_concepts)


def test_no_syspath_hacks_required() -> None:
    """Re-importing must not depend on a previously-mutated sys.path entry."""
    snapshot = list(sys.path)
    # Drop the obvious in-tree paths that an editable install would
    # legitimately add. If the import still works, we're good.
    pruned = [p for p in snapshot if "ontology_vectorizer" not in p]
    sys.path[:] = pruned
    try:
        # Force a clean import; ``importlib.reload`` is not enough because
        # the package is already cached.
        for name in list(sys.modules):
            if name == "ontology_vectorizer" or name.startswith(
                "ontology_vectorizer."
            ):
                del sys.modules[name]
        import ontology_vectorizer  # noqa: F401
    finally:
        sys.path[:] = snapshot
