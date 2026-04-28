"""RDF terms in the IR: IRIs, prefixed names, variables, literals."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from graph_mcp.models.literals import (
    ABSOLUTE_IRI_REGEX,
    LANG_TAG_REGEX,
    PREFIX_REGEX,
    PREFIXED_LOCAL_REGEX,
    VAR_NAME_REGEX,
)


class _StrictBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False, strict=True)


class Var(_StrictBase):
    """A SPARQL variable. Rendered as ``?name``."""

    kind: Literal["var"] = "var"
    name: str

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        if not VAR_NAME_REGEX.match(v):
            raise ValueError(f"invalid variable name: {v!r}")
        return v


class Iri(_StrictBase):
    """An absolute IRI."""

    kind: Literal["iri"] = "iri"
    value: str

    @field_validator("value")
    @classmethod
    def _check_iri(cls, v: str) -> str:
        if not ABSOLUTE_IRI_REGEX.match(v):
            raise ValueError(f"not an absolute IRI: {v!r}")
        if "<" in v or ">" in v:
            raise ValueError(f"IRI contains forbidden delimiters: {v!r}")
        return v


class PrefixedName(_StrictBase):
    """A ``prefix:local`` reference. Resolved against the plan's prefix map."""

    kind: Literal["prefixed_name"] = "prefixed_name"
    prefix: str
    local: str

    @field_validator("prefix")
    @classmethod
    def _check_prefix(cls, v: str) -> str:
        if not PREFIX_REGEX.match(v):
            raise ValueError(f"invalid prefix: {v!r}")
        return v

    @field_validator("local")
    @classmethod
    def _check_local(cls, v: str) -> str:
        if not PREFIXED_LOCAL_REGEX.match(v):
            raise ValueError(f"invalid prefixed-name local part: {v!r}")
        return v


class LiteralValue(_StrictBase):
    """A typed RDF literal.

    Exactly one of ``datatype`` or ``lang`` may be set; setting both is invalid.
    """

    kind: Literal["literal"] = "literal"
    value: str | int | float | bool
    datatype: str | None = None
    lang: str | None = None

    @field_validator("datatype")
    @classmethod
    def _check_datatype(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not ABSOLUTE_IRI_REGEX.match(v):
            raise ValueError(f"datatype must be an absolute IRI: {v!r}")
        return v

    @field_validator("lang")
    @classmethod
    def _check_lang(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not LANG_TAG_REGEX.match(v):
            raise ValueError(f"invalid language tag: {v!r}")
        return v

    def model_post_init(self, __context: object) -> None:
        if self.datatype and self.lang:
            raise ValueError("literal cannot have both datatype and lang")


# An IRI-like reference: either absolute IRI or prefixed name.
IriRef = Annotated[Iri | PrefixedName, Field(discriminator="kind")]


# Any RDF term usable in the subject/predicate/object position of a triple
# (excluding property paths, which are handled separately).
RdfTerm = Annotated[
    Var | Iri | PrefixedName | LiteralValue,
    Field(discriminator="kind"),
]


class Prefix(_StrictBase):
    """Declares ``PREFIX prefix: <iri>`` for the rendered query."""

    prefix: str
    iri: str

    @field_validator("prefix")
    @classmethod
    def _check_prefix(cls, v: str) -> str:
        if not PREFIX_REGEX.match(v):
            raise ValueError(f"invalid prefix: {v!r}")
        return v

    @field_validator("iri")
    @classmethod
    def _check_iri(cls, v: str) -> str:
        if not ABSOLUTE_IRI_REGEX.match(v):
            raise ValueError(f"prefix IRI must be absolute: {v!r}")
        return v
