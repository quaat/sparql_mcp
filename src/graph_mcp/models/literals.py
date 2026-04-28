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

# Common, well-known prefixes safe to expose by default.
DEFAULT_PREFIXES: dict[str, str] = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "dct": "http://purl.org/dc/terms/",
    "foaf": "http://xmlns.com/foaf/0.1/",
}
