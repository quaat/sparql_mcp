"""Shared regex constants and prefix defaults."""

from __future__ import annotations

import re

# Variable name: alpha_underscore + alphanumeric/underscore. The leading '?' is
# part of the rendered SPARQL form, not the IR field.
VAR_NAME_REGEX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")

# SPARQL PNAME_NS: a sequence of NCName chars (we tighten to alphanumeric+underscore).
PREFIX_REGEX = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-.]{0,63}$|^$")

# Local part of a prefixed name. We are deliberately conservative — the renderer
# will percent-encode anything outside this set if it ever needs to.
PREFIXED_LOCAL_REGEX = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_\-.~]{0,255}$")

# IETF BCP47 language tag (loose match — the validator does not normalize).
LANG_TAG_REGEX = re.compile(r"^[A-Za-z]{1,8}(?:-[A-Za-z0-9]{1,8})*$")

# Acceptable absolute IRI scheme. We intentionally reject relative IRIs.
ABSOLUTE_IRI_REGEX = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:[^\s<>\"{}|\\^`]+$")

# Common, well-known prefixes safe to expose by default. These are also
# the set the validator and renderer protect from override.
DEFAULT_PREFIXES: dict[str, str] = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "dct": "http://purl.org/dc/terms/",
    "foaf": "http://xmlns.com/foaf/0.1/",
}

# Domain-specific prefixes the eval harness advertises to schema discovery
# and to the prompt builder when a dataset is known to use them. They are
# *not* added to :data:`DEFAULT_PREFIXES` so the validator's default-prefix
# override protection stays exactly as before — these can be passed through
# :class:`graph_mcp.graph.schema_discovery.SparqlDiscoveryConfig.base_prefixes`
# and embedded in plans without locking out users who define their own.
OCEAN_KG_PREFIXES: dict[str, str] = {
    "dcat": "http://www.w3.org/ns/dcat#",
    "dcterms": "http://purl.org/dc/terms/",
    "geo": "http://www.opengis.net/ont/geosparql#",
    "prov": "http://www.w3.org/ns/prov#",
    "sosa": "http://www.w3.org/ns/sosa/",
    "app": "https://example.org/ontology/app#",
    "var": "https://example.org/ocean-demo/id/observable-property/",
}
