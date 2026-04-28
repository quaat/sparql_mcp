"""Shared fixtures."""

from __future__ import annotations

import pytest

from graph_mcp.compiler import QueryPlanValidator, SparqlRenderer
from graph_mcp.config import Settings
from graph_mcp.security import SecurityPolicy


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def policy(settings: Settings) -> SecurityPolicy:
    return SecurityPolicy.from_settings(settings)


@pytest.fixture
def validator(policy: SecurityPolicy) -> QueryPlanValidator:
    return QueryPlanValidator(policy)


@pytest.fixture
def renderer(policy: SecurityPolicy) -> SparqlRenderer:
    return SparqlRenderer(policy)


@pytest.fixture
def permissive_settings() -> Settings:
    return Settings(allow_unbounded_paths=True)


@pytest.fixture
def permissive_policy(permissive_settings: Settings) -> SecurityPolicy:
    return SecurityPolicy.from_settings(permissive_settings)
