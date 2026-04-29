"""Lexical term resolver.

Maps natural-language mentions to schema IRIs using deterministic string
matching (label, prefixed name, local name, aliases). The implementation is
intentionally simple — the goal is to give the LLM stable, ranked candidates
without depending on embeddings or the network.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from graph_mcp.graph.schema_discovery import (
    ClassTerm,
    IndividualTerm,
    NamedGraphTerm,
    PropertyTerm,
    SchemaProvider,
    SchemaTerm,
)

TermKind = Literal["class", "property", "individual", "graph", "unknown"]


class TermCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mention: str
    iri: str
    prefixed_name: str | None = None
    kind: TermKind
    label: str | None = None
    score: float
    explanation: str


class TermResolutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[TermCandidate] = Field(default_factory=list)


_NORM_RE = re.compile(r"[^a-z0-9]+")

# Deliberately small, deterministic lemma table for common class nouns.
# Keep the LLM-side prompt strict on unresolved mentions; the right place
# to bridge "people" → "person" is here, before the resolver scores.
_LEMMA_OVERRIDES: dict[str, str] = {
    "people": "person",
    "persons": "person",
    "companies": "company",
    "organisations": "organisation",
    "organizations": "organization",
    "projects": "project",
    "employees": "employee",
    "graphs": "graph",
}


def _lemma_token(token: str) -> str:
    if token in _LEMMA_OVERRIDES:
        return _LEMMA_OVERRIDES[token]
    # Conservative regular-plural fallback. Skip:
    # - short words (avoid spurious 1-letter strips)
    # - ``ss``      → ``class``      must not become ``cla``
    # - ``us``      → ``status``     must not become ``statu``
    # - ``is``      → ``analysis``   must not become ``analysi``
    # - ``os``      → ``chaos``      must not become ``chao``
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("s") and not token.endswith(
        ("ss", "us", "is", "os")
    ):
        return token[:-1]
    return token


def _normalize(s: str) -> str:
    raw = _NORM_RE.sub(" ", s.lower()).strip()
    if not raw:
        return raw
    return " ".join(_lemma_token(part) for part in raw.split())


def _split_camel(s: str) -> str:
    return re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)


def _candidate_strings(term: SchemaTerm) -> list[str]:
    out: list[str] = []
    if term.label:
        out.append(term.label)
    out.extend(term.aliases)
    if term.prefixed_name:
        # Local name (e.g. ex:worksFor → "works for")
        local = term.prefixed_name.split(":", 1)[-1]
        out.append(_split_camel(local))
    # Last-segment of the IRI as a final fallback.
    last = re.split(r"[#/]", term.iri.rstrip("#/"))[-1]
    out.append(_split_camel(last))
    return out


def _score(mention: str, candidate: str) -> float:
    nm = _normalize(mention)
    nc = _normalize(candidate)
    if not nm or not nc:
        return 0.0
    if nm == nc:
        return 1.0
    if nm in nc or nc in nm:
        return 0.85
    return SequenceMatcher(None, nm, nc).ratio()


class TermResolver:
    """Deterministic resolver. Use :meth:`resolve` from the MCP tool path."""

    def __init__(self, schema: SchemaProvider) -> None:
        self.schema = schema

    def resolve(
        self,
        mentions: list[str],
        *,
        expected_kinds: list[TermKind] | None = None,
        limit: int = 10,
    ) -> TermResolutionResult:
        snap = self.schema.snapshot()
        kinds = (
            set(expected_kinds)
            if expected_kinds
            else {
                "class",
                "property",
                "individual",
                "graph",
            }
        )

        all_candidates: list[TermCandidate] = []
        for mention in mentions:
            scored: list[TermCandidate] = []
            if "class" in kinds:
                for c in snap.classes:
                    scored.append(self._score_term(mention, c, "class"))
            if "property" in kinds:
                for p in snap.properties:
                    scored.append(self._score_term(mention, p, "property"))
            if "individual" in kinds:
                for ind in snap.individuals:
                    scored.append(self._score_term(mention, ind, "individual"))
            if "graph" in kinds:
                for g in snap.named_graphs:
                    scored.append(self._score_graph(mention, g))
            scored.sort(key=lambda c: c.score, reverse=True)
            # Require a minimum match quality. SequenceMatcher returns small
            # ratios for unrelated tokens, so we cut off below 0.4.
            top = [c for c in scored if c.score >= 0.4][:limit]
            if not top:
                top = [
                    TermCandidate(
                        mention=mention,
                        iri="",
                        prefixed_name=None,
                        kind="unknown",
                        label=None,
                        score=0.0,
                        explanation="no schema term matched this mention",
                    )
                ]
            all_candidates.extend(top)
        return TermResolutionResult(candidates=all_candidates)

    def _score_term(
        self,
        mention: str,
        term: ClassTerm | PropertyTerm | IndividualTerm,
        kind: TermKind,
    ) -> TermCandidate:
        cand_strings = _candidate_strings(term)
        best = 0.0
        best_src = ""
        for s in cand_strings:
            sc = _score(mention, s)
            if sc > best:
                best = sc
                best_src = s
        return TermCandidate(
            mention=mention,
            iri=term.iri,
            prefixed_name=term.prefixed_name,
            kind=kind,
            label=term.label,
            score=best,
            explanation=f"matched {kind} via {best_src!r}",
        )

    def _score_graph(self, mention: str, g: NamedGraphTerm) -> TermCandidate:
        cands: list[str | None] = [g.label, g.iri.rstrip("#/").rsplit("/", 1)[-1]]
        if g.prefixed_name:
            cands.append(g.prefixed_name)
            cands.append(_split_camel(g.prefixed_name.split(":", 1)[-1]))
        best = 0.0
        best_src = ""
        for s in cands:
            if not s:
                continue
            sc = _score(mention, s)
            if sc > best:
                best = sc
                best_src = s
        return TermCandidate(
            mention=mention,
            iri=g.iri,
            prefixed_name=g.prefixed_name,
            kind="graph",
            label=g.label,
            score=best,
            explanation=f"matched graph via {best_src!r}",
        )
