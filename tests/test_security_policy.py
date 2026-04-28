"""Tests for security policy and config."""

from __future__ import annotations

from graph_mcp.config import Settings
from graph_mcp.security.policy import SecurityPolicy


def test_default_policy() -> None:
    policy = SecurityPolicy.from_settings(Settings())
    assert policy.default_limit == 100
    assert policy.max_limit == 1000
    assert policy.timeout_ms == 5000
    assert not policy.enable_raw_sparql
    assert not policy.allow_unbounded_paths


def test_graph_allowlist() -> None:
    s = Settings(allowed_graphs="http://a/,http://b/")  # type: ignore[arg-type]
    p = SecurityPolicy.from_settings(s)
    assert p.is_graph_allowed("http://a/")
    assert p.is_graph_allowed("http://b/")
    assert not p.is_graph_allowed("http://c/")


def test_empty_graph_allowlist_is_open() -> None:
    p = SecurityPolicy.from_settings(Settings())
    assert p.is_graph_allowed("http://anything/")


def test_service_default_closed() -> None:
    p = SecurityPolicy.from_settings(Settings())
    assert not p.is_service_allowed("http://x/")
    s = Settings(allowed_service_endpoints="http://x/")  # type: ignore[arg-type]
    p2 = SecurityPolicy.from_settings(s)
    assert p2.is_service_allowed("http://x/")
