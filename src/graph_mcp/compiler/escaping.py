"""Safe escaping for SPARQL string literals and IRIs.

These helpers are the *only* place untrusted text is converted to SPARQL
syntax. Treat the rest of the renderer as if it can be hostile: each value
goes through one of these functions before reaching the output buffer.
"""

from __future__ import annotations

# SPARQL 1.1 string literal escapes.
_STRING_ESCAPES: dict[str, str] = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\b": "\\b",
    "\f": "\\f",
}


def escape_string_literal(value: str) -> str:
    """Escape a Python string for use inside ``"..."`` SPARQL literals."""
    out = []
    for ch in value:
        out.append(_STRING_ESCAPES.get(ch, ch))
    return "".join(out)


def escape_iri(iri: str) -> str:
    r"""Escape an IRI for use inside ``<...>``.

    SPARQL 1.1 forbids the characters ``<>"{}|\^``` (and unescaped whitespace)
    inside an IRI reference. We reject any string that contains them — silent
    escaping would change the IRI's identity, which is unsafe.
    """
    forbidden = set('<>"{}|\\^`')
    for ch in iri:
        if ch in forbidden or ch.isspace():
            raise ValueError(f"IRI contains forbidden character {ch!r}: {iri!r}")
    return iri


def escape_lang_tag(tag: str) -> str:
    if not tag.replace("-", "").isalnum():
        raise ValueError(f"invalid language tag: {tag!r}")
    return tag
