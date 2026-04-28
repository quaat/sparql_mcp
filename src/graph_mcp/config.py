"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_csv(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [v for v in value if v]
    return [v.strip() for v in value.split(",") if v.strip()]


class Settings(BaseSettings):
    """Process-wide configuration. All fields are read from `GRAPH_MCP_*` env vars."""

    model_config = SettingsConfigDict(
        env_prefix="GRAPH_MCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    endpoint_url: str | None = None

    default_limit: Annotated[int, Field(gt=0, le=10_000)] = 100
    max_limit: Annotated[int, Field(gt=0, le=100_000)] = 1000
    timeout_ms: Annotated[int, Field(gt=0, le=600_000)] = 5000

    allowed_graphs: list[str] = []
    allowed_service_endpoints: list[str] = []

    enable_raw_sparql: bool = False

    max_triple_patterns: Annotated[int, Field(gt=0, le=10_000)] = 200
    max_query_depth: Annotated[int, Field(gt=0, le=64)] = 8
    max_property_path_complexity: Annotated[int, Field(gt=0, le=256)] = 16
    allow_unbounded_paths: bool = False

    allowed_path_predicates: list[str] = []
    """When non-empty, every property-path predicate IRI must appear in this list.

    Useful when the host wants to restrict which predicates can be traversed
    in property paths (and especially in unbounded ``+``/``*`` paths).
    """

    allow_default_prefix_override: bool = False
    """Allow plans to redefine built-in prefixes (rdf, rdfs, xsd, etc.).

    Disabled by default — a plan that redefines ``xsd:`` to point at a
    different IRI is almost always a mistake or an attempt to confuse the
    renderer's datatype-IRI compaction logic.
    """

    local_graph_file: Path | None = None

    # --- Schema discovery -------------------------------------------------

    schema_provider: Annotated[str, Field(pattern=r"^(static|sparql|auto)$")] = "auto"
    """Which :class:`SchemaProvider` to instantiate.

    - ``static`` — use ``StaticSchemaProvider`` (empty by default).
    - ``sparql`` — use ``SparqlSchemaProvider``; requires an endpoint.
    - ``auto`` — use ``SparqlSchemaProvider`` when ``endpoint_url`` or
      ``local_graph_file`` is configured, else fall back to static.
    """

    schema_cache_ttl_seconds: Annotated[float, Field(ge=0)] = 300.0
    schema_discovery_timeout_ms: Annotated[int, Field(gt=0, le=600_000)] = 10_000
    schema_max_classes: Annotated[int, Field(gt=0, le=10_000)] = 200
    schema_max_properties: Annotated[int, Field(gt=0, le=10_000)] = 500
    schema_max_individuals: Annotated[int, Field(gt=0, le=10_000)] = 200
    schema_max_named_graphs: Annotated[int, Field(gt=0, le=10_000)] = 200
    schema_discovery_on_startup: bool = True

    log_level: str = "INFO"

    @field_validator(
        "allowed_graphs",
        "allowed_service_endpoints",
        "allowed_path_predicates",
        mode="before",
    )
    @classmethod
    def _coerce_csv(cls, value: object) -> list[str]:
        return _split_csv(value)  # type: ignore[arg-type]

    @field_validator("max_limit")
    @classmethod
    def _max_ge_default(cls, v: int, info: object) -> int:
        # Note: default_limit is validated independently; we rely on cross-checks at use time.
        return v


def load_settings() -> Settings:
    """Load settings from environment / .env. A fresh call re-reads env vars."""
    return Settings()
