"""Schema providers: declared prefixes, classes, properties, named graphs."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class SchemaTerm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iri: str
    prefixed_name: str | None = None
    label: str | None = None
    aliases: list[str] = Field(default_factory=list)
    description: str | None = None


class ClassTerm(SchemaTerm):
    pass


class PropertyTerm(SchemaTerm):
    domain: list[str] = Field(default_factory=list)
    range: list[str] = Field(default_factory=list)


class IndividualTerm(SchemaTerm):
    types: list[str] = Field(default_factory=list)


class NamedGraphTerm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iri: str
    label: str | None = None
    description: str | None = None


class ExamplePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    plan: dict[str, object]


class SchemaSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prefixes: dict[str, str] = Field(default_factory=dict)
    classes: list[ClassTerm] = Field(default_factory=list)
    properties: list[PropertyTerm] = Field(default_factory=list)
    individuals: list[IndividualTerm] = Field(default_factory=list)
    named_graphs: list[NamedGraphTerm] = Field(default_factory=list)
    examples: list[ExamplePlan] = Field(default_factory=list)


class SchemaProvider(Protocol):
    def snapshot(self) -> SchemaSnapshot:
        ...


class StaticSchemaProvider:
    """Schema provider backed by an in-memory snapshot."""

    def __init__(self, snapshot: SchemaSnapshot) -> None:
        self._snapshot = snapshot

    def snapshot(self) -> SchemaSnapshot:
        return self._snapshot

    @classmethod
    def empty(cls) -> StaticSchemaProvider:
        return cls(SchemaSnapshot())
