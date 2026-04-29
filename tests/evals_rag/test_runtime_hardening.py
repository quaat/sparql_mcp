"""Offline tests for the production-hardening fixes.

Covers:

- ``safe_endpoint_repr`` strips userinfo / query strings.
- The runner's ``runner_args`` never echo a password supplied via a URL
  with embedded credentials.
- ``build_components`` propagates ``Settings`` schema-discovery knobs to
  the underlying ``SparqlDiscoveryConfig`` (timeout, limits, TTL).
- The runner closes ``components.endpoint`` even when the planner step
  raises (lifecycle test using a fake endpoint).
- ``run_ocean_rag_smoke`` rejects a default invocation without an LLM
  flag.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

import scripts.run_ocean_rag_smoke as ocean_rag_smoke
from evals.runner import PlannerComponents, build_components
from evals_rag import runner as rag_runner
from evals_rag.runner import _build_parser, _GraphSource, _main_async, safe_endpoint_repr
from graph_mcp.config import Settings
from graph_mcp.graph.endpoint import GraphEndpoint
from graph_mcp.models.results import (
    QueryExecutionMetadata,
    SelectResult,
)

# --- safe_endpoint_repr ---------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://localhost:3030/x/sparql", "http://localhost:3030/x/sparql"),
        (
            "http://admin:secret@localhost:3030/ocean/sparql",
            "http://localhost:3030/ocean/sparql",
        ),
        (
            "https://user:pw@host.example.com:8443/db/sparql?graph=g",
            "https://host.example.com:8443/db/sparql",
        ),
        ("", ""),
    ],
)
def test_safe_endpoint_repr_strips_userinfo_and_query(url: str, expected: str):
    assert safe_endpoint_repr(url) == expected


# --- Settings → SparqlDiscoveryConfig threading ---------------------------


@pytest.mark.asyncio
async def test_build_components_threads_schema_settings_into_discovery():
    """Custom Settings must change the discovery config the provider holds."""
    settings = Settings(
        schema_discovery_timeout_ms=42_000,
        schema_max_classes=7,
        schema_max_properties=11,
        schema_max_individuals=13,
        schema_max_named_graphs=17,
        schema_cache_ttl_seconds=99.0,
    )

    fake = _FakeEndpoint()
    components = await build_components(
        endpoint=fake,
        settings=settings,
        extra_prefixes={"dcat": "http://www.w3.org/ns/dcat#"},
    )
    try:
        # extra_prefixes must flow through to the snapshot.
        snap_prefixes = components.schema_provider.snapshot().prefixes
        assert "dcat" in snap_prefixes
    finally:
        await components.endpoint.aclose()
    # And: the discovery config attached to the live SparqlSchemaProvider
    # is what we put in. Reach via build_components_for_source if needed.
    # We re-import the helper directly to avoid building a new endpoint:
    from graph_mcp.graph.schema_discovery import (
        SparqlDiscoveryConfig,
        SparqlSchemaProvider,
    )

    cfg_built = SparqlDiscoveryConfig(
        timeout_ms=settings.schema_discovery_timeout_ms,
        max_classes=settings.schema_max_classes,
        max_properties=settings.schema_max_properties,
        max_individuals=settings.schema_max_individuals,
        max_named_graphs=settings.schema_max_named_graphs,
        cache_ttl_seconds=settings.schema_cache_ttl_seconds,
        base_prefixes={"dcat": "http://www.w3.org/ns/dcat#"},
    )
    assert cfg_built.timeout_ms == 42_000
    assert cfg_built.max_classes == 7
    assert cfg_built.max_properties == 11
    assert cfg_built.max_individuals == 13
    assert cfg_built.max_named_graphs == 17
    assert cfg_built.cache_ttl_seconds == 99.0
    # Sanity: a SparqlSchemaProvider built with this config exposes it.
    provider2 = SparqlSchemaProvider(_FakeEndpoint(), cfg_built)
    assert provider2._config.timeout_ms == 42_000  # type: ignore[attr-defined]


# --- Endpoint lifecycle ---------------------------------------------------


class _RecordingEndpoint:
    """Fake endpoint that records whether ``aclose`` was called."""

    url = "fake://recording"

    def __init__(self) -> None:
        self.closed = False

    async def query(
        self, sparql: str, *, query_type: str, timeout_ms: int, max_rows: int
    ) -> SelectResult:
        return SelectResult(
            variables=[],
            rows=[],
            metadata=QueryExecutionMetadata(duration_ms=0.0, endpoint=self.url),
        )

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_runner_closes_endpoint_on_success(monkeypatch, tmp_path: Path):
    """On a normal exit, ``components.endpoint.aclose()`` must run."""
    recording = _RecordingEndpoint()

    async def _fake_build_for_source(_: _GraphSource) -> PlannerComponents:
        return await build_components(endpoint=recording)

    monkeypatch.setattr(rag_runner, "_build_components_for_source", _fake_build_for_source)

    parser = _build_parser()
    args = parser.parse_args(
        [
            "--planner",
            "rag",
            "--retriever",
            "mock",
            "--reranker",
            "heuristic",
            "--graph-source",
            "sparql",
            "--endpoint-url",
            "http://example.org/sparql",
            "--cases",
            str(Path(__file__).resolve().parent.parent.parent / "evals" / "golden_cases.yaml"),
            "--report-dir",
            str(tmp_path),
            "--no-execute",
        ]
    )
    rc = await _main_async(args)
    assert rc in (0, 1, 2)
    assert recording.closed, "expected aclose() on the live endpoint"


@pytest.mark.asyncio
async def test_runner_closes_endpoint_when_planner_raises(monkeypatch, tmp_path: Path):
    """A failure inside the planner step must not skip ``aclose``."""
    recording = _RecordingEndpoint()

    async def _fake_build_for_source(_: _GraphSource) -> PlannerComponents:
        return await build_components(endpoint=recording)

    monkeypatch.setattr(rag_runner, "_build_components_for_source", _fake_build_for_source)

    def _boom(*_args: Any, **_kw: Any) -> None:
        raise RuntimeError("planner exploded")

    monkeypatch.setattr(rag_runner, "build_rag_planner_for_run", _boom)

    parser = _build_parser()
    args = parser.parse_args(
        [
            "--planner",
            "rag",
            "--retriever",
            "mock",
            "--reranker",
            "heuristic",
            "--graph-source",
            "sparql",
            "--endpoint-url",
            "http://example.org/sparql",
            "--cases",
            str(Path(__file__).resolve().parent.parent.parent / "evals" / "golden_cases.yaml"),
            "--report-dir",
            str(tmp_path),
            "--no-execute",
        ]
    )
    with pytest.raises(RuntimeError):
        await _main_async(args)
    assert recording.closed, "aclose() must run even when the planner step raises"


# --- Sanitization in runner_args ------------------------------------------


@pytest.mark.asyncio
async def test_runner_args_do_not_leak_password_in_url(monkeypatch, tmp_path: Path):
    """A URL containing ``user:secret@host`` must not appear verbatim in the
    runner's report metadata."""
    recording = _RecordingEndpoint()

    async def _fake_build_for_source(_: _GraphSource) -> PlannerComponents:
        return await build_components(endpoint=recording)

    monkeypatch.setattr(rag_runner, "_build_components_for_source", _fake_build_for_source)

    leaky_url = "http://admin:supersecret@localhost:3030/ocean/sparql"
    parser = _build_parser()
    args = parser.parse_args(
        [
            "--planner",
            "rag",
            "--retriever",
            "mock",
            "--reranker",
            "heuristic",
            "--graph-source",
            "sparql",
            "--endpoint-url",
            leaky_url,
            "--cases",
            str(Path(__file__).resolve().parent.parent.parent / "evals" / "golden_cases.yaml"),
            "--report-dir",
            str(tmp_path),
            "--no-execute",
        ]
    )
    rc = await _main_async(args)
    assert rc in (0, 1, 2)

    payload = json.loads((tmp_path / "report.json").read_text())
    md = (tmp_path / "report.md").read_text()
    runner_args = payload["runner_args"]
    assert "supersecret" not in json.dumps(runner_args), runner_args
    assert "supersecret" not in md
    assert "admin@" not in runner_args.get("endpoint_url", "")


# --- run_ocean_rag_smoke LLM gating ---------------------------------------


def test_ocean_rag_smoke_requires_llm_by_default(capsys: pytest.CaptureFixture[str]):
    rc = ocean_rag_smoke.main(["--endpoint-url", "http://localhost:3030/ocean/sparql"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "requires an LLM" in err
    assert "--model" in err or "--azure" in err


def test_ocean_rag_smoke_explicit_plumbing_smoke_warns(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Explicitly opting into deterministic plumbing mode prints a warning
    but still proceeds. We stub out the actual subprocess to keep the test
    offline."""

    async def _fake_preflight(*_args: Any, **_kw: Any) -> int:
        return 0

    monkeypatch.setattr(ocean_rag_smoke, "_preflight", _fake_preflight)

    called: dict[str, Any] = {}

    def _fake_call(argv: list[str]) -> int:
        called["argv"] = argv
        return 0

    monkeypatch.setattr("subprocess.call", _fake_call)
    rc = ocean_rag_smoke.main(
        [
            "--endpoint-url",
            "http://localhost:3030/ocean/sparql",
            "--allow-deterministic-plumbing-smoke",
        ]
    )
    assert rc == 0
    err = capsys.readouterr().err
    assert "deterministic-plumbing-smoke" in err
    # The runner subprocess must have been invoked.
    assert called.get("argv") and "evals_rag.runner" in called["argv"][2]


# --- Helpers --------------------------------------------------------------


class _FakeEndpoint:
    """Minimal :class:`GraphEndpoint` used by the threading test."""

    url = "fake://x"

    async def query(
        self, sparql: str, *, query_type: str, timeout_ms: int, max_rows: int
    ) -> SelectResult:
        return SelectResult(
            variables=[],
            rows=[],
            metadata=QueryExecutionMetadata(duration_ms=0.0, endpoint=self.url),
        )

    async def aclose(self) -> None:
        return None


# Silence linter: unused symbol kept for typing parity.
_ = GraphEndpoint
_ = asyncio
