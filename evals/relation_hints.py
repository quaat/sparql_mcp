"""Schema-aware relation hints for the planner workflow.

The planner often fails on phrases like "their company", "people per
company", or "oldest person" because the schema doesn't tell it which
property connects the resolved classes. This module produces a small,
human-readable list of inferred hints that the planner can rely on:

- ``ex:worksFor connects ex:Person → ex:Company``
- ``ex:age is observed on ex:Person with xsd:integer values``

Hints come from the schema's ``observed_domain`` / ``observed_range`` plus
small heuristics on the question text. They are advisory: the planner
still picks terms from the candidate table, but a hint can lift the
planner over an inference gap that would otherwise force clarification.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from graph_mcp.graph.schema_discovery import (
    PropertyTerm,
    SchemaSnapshot,
)
from graph_mcp.graph.term_resolver import TermCandidate


class RelationHint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    property_iri: str
    prefixed_name: str | None = None
    subject_type: str | None = None
    object_type: str | None = None
    reason: str
    score: float = Field(ge=0.0, le=1.0)


_AGE_CUES = re.compile(r"\b(oldest|youngest|age|years|year-old)\b", re.IGNORECASE)
_DATE_CUES = re.compile(
    r"\b(joined|hired|started|date|after|before)\b",
    re.IGNORECASE,
)
_GROUPING_CUES = re.compile(
    r"\b(per|each|by|grouped)\b",
    re.IGNORECASE,
)
_EMPLOYMENT_CUES = re.compile(
    r"\b(employee|employees|employer|works\s+for|works\s+at|"
    r"their\s+company|their\s+employer|per\s+company|each\s+company|"
    r"company\s+of|in\s+a\s+company|at\s+(?:the\s+)?company)\b",
    re.IGNORECASE,
)
_KNOWS_CUES = re.compile(r"\b(knows|connections?|friends?|acquaintances?)\b", re.IGNORECASE)


def _selected_class_iris(selected: list[TermCandidate]) -> set[str]:
    return {c.iri for c in selected if c.kind == "class"}


def _selected_class_names(selected: list[TermCandidate]) -> set[str]:
    return {(c.label or "").lower() for c in selected if c.kind == "class"} | {
        (c.iri.rstrip("#/").rsplit("/", 1)[-1]).lower() for c in selected if c.kind == "class"
    }


def _selected_individual_iris(selected: list[TermCandidate]) -> set[str]:
    return {c.iri for c in selected if c.kind == "individual"}


def _connects(prop: PropertyTerm, src: set[str], dst: set[str]) -> bool:
    """Does the property connect any class in ``src`` to any class in ``dst``?"""
    sd = set(prop.domain) | set(prop.observed_domain)
    sr = set(prop.range) | set(prop.observed_range)
    return bool(sd & src) and bool(sr & dst)


def _short(iri: str, prefixes: dict[str, str]) -> str:
    for prefix, base in prefixes.items():
        if iri.startswith(base):
            return f"{prefix}:{iri[len(base) :]}"
    return iri


def infer_relation_hints(
    question: str,
    selected_terms: list[TermCandidate],
    schema: SchemaSnapshot,
) -> list[RelationHint]:
    """Return relation hints for ``question`` given the resolved candidates.

    Hints are scored 0..1 by how confident the heuristic is. The planner
    sees them as a numbered list — only high-confidence hints should be
    used to skip clarification.
    """
    hints: list[RelationHint] = []
    seen: set[tuple[str, str, str]] = set()
    prefixes = schema.prefixes
    selected_class_iris = _selected_class_iris(selected_terms)
    selected_class_names = _selected_class_names(selected_terms)
    selected_individual_iris = _selected_individual_iris(selected_terms)

    def _add(hint: RelationHint) -> None:
        key = (hint.property_iri, hint.subject_type or "", hint.object_type or "")
        if key in seen:
            return
        seen.add(key)
        hints.append(hint)

    # 1) Employment hints — when the question mentions employment/company
    # cues, prefer properties that connect Person → Company.
    if _EMPLOYMENT_CUES.search(question):
        person_iris = {
            c.iri for c in schema.classes if (c.label or "").lower() in {"person", "people"}
        }
        company_iris = {
            c.iri
            for c in schema.classes
            if (c.label or "").lower() in {"company", "organization", "organisation", "employer"}
        }
        # If the question names individuals (Alice, Bob), allow the hint
        # even when the resolved-term set lacks the Person class.
        if selected_individual_iris:
            person_iris |= {c.iri for c in selected_terms if c.kind == "individual"}
        for prop in schema.properties:
            if _connects(prop, person_iris, company_iris):
                domain_match = person_iris & (set(prop.domain) | set(prop.observed_domain))
                range_match = company_iris & (set(prop.range) | set(prop.observed_range))
                _add(
                    RelationHint(
                        property_iri=prop.iri,
                        prefixed_name=prop.prefixed_name or _short(prop.iri, prefixes),
                        subject_type=next(iter(domain_match), None),
                        object_type=next(iter(range_match), None),
                        reason=(
                            "Question implies employment / 'their company' / 'per company'; "
                            "this property connects Person → Company."
                        ),
                        score=0.95,
                    )
                )

    # 2) Age cues
    if _AGE_CUES.search(question):
        for prop in schema.properties:
            label = (prop.label or "").lower()
            local = (prop.prefixed_name or prop.iri).rsplit(":", 1)[-1].rsplit("/", 1)[-1].lower()
            if "age" in (label, local):
                _add(
                    RelationHint(
                        property_iri=prop.iri,
                        prefixed_name=prop.prefixed_name or _short(prop.iri, prefixes),
                        subject_type=next(iter(prop.observed_domain or prop.domain or []), None),
                        object_type=next(iter(prop.range or prop.observed_range or []), None),
                        reason="Question asks about age / oldest / youngest.",
                        score=0.9,
                    )
                )

    # 3) Date cues
    if _DATE_CUES.search(question):
        for prop in schema.properties:
            label = (prop.label or "").lower()
            local = (prop.prefixed_name or prop.iri).rsplit(":", 1)[-1].rsplit("/", 1)[-1].lower()
            if any(c in label or c in local for c in ("joined", "hired", "started", "date")):
                _add(
                    RelationHint(
                        property_iri=prop.iri,
                        prefixed_name=prop.prefixed_name or _short(prop.iri, prefixes),
                        subject_type=next(iter(prop.observed_domain or prop.domain or []), None),
                        object_type=next(iter(prop.range or prop.observed_range or []), None),
                        reason="Question asks about a date / when something happened.",
                        score=0.85,
                    )
                )

    # 4) Knows / acquaintance cues
    if _KNOWS_CUES.search(question):
        for prop in schema.properties:
            local = (prop.prefixed_name or prop.iri).rsplit(":", 1)[-1].rsplit("/", 1)[-1].lower()
            label = (prop.label or "").lower()
            if local == "knows" or label == "knows":
                _add(
                    RelationHint(
                        property_iri=prop.iri,
                        prefixed_name=prop.prefixed_name or _short(prop.iri, prefixes),
                        subject_type=next(iter(prop.observed_domain or prop.domain or []), None),
                        object_type=next(iter(prop.range or prop.observed_range or []), None),
                        reason="Question mentions 'knows' / connections.",
                        score=0.9,
                    )
                )

    # 5) Generic class-pair hints — when both ends are in the resolved class
    # set and exactly one property connects them, hint it. This covers
    # paraphrased "X of Y" questions without being too aggressive.
    if len(selected_class_iris) >= 2:
        cs = list(selected_class_iris)
        for i, src in enumerate(cs):
            for dst in cs[i + 1 :]:
                connectors = [p for p in schema.properties if _connects(p, {src}, {dst})]
                if len(connectors) == 1:
                    prop = connectors[0]
                    _add(
                        RelationHint(
                            property_iri=prop.iri,
                            prefixed_name=prop.prefixed_name or _short(prop.iri, prefixes),
                            subject_type=src,
                            object_type=dst,
                            reason=(
                                f"Only observed property connecting {_short(src, prefixes)} → "
                                f"{_short(dst, prefixes)}."
                            ),
                            score=0.8,
                        )
                    )

    # 6) Grouping cues (without an employment cue) — if the question says
    # "per X" and X is a class, prefer the unique connecting property.
    if _GROUPING_CUES.search(question) and selected_class_names:
        for prop in schema.properties:
            for cls in schema.classes:
                cls_name = (cls.label or "").lower()
                if (
                    cls_name
                    and cls_name in selected_class_names
                    and _connects(prop, set(), {cls.iri})
                ):
                    _add(
                        RelationHint(
                            property_iri=prop.iri,
                            prefixed_name=prop.prefixed_name or _short(prop.iri, prefixes),
                            subject_type=None,
                            object_type=cls.iri,
                            reason=(
                                f"'per {cls_name}' grouping cue + this property targets {cls_name}."
                            ),
                            score=0.75,
                        )
                    )

    return hints


def format_hints_block(hints: list[RelationHint]) -> str:
    """Render hints for inclusion in the LLM prompt."""
    if not hints:
        return "(no relation hints inferred)"
    lines = ["Inferred relation hints (use these when the question implies an unnamed relation):"]
    for h in sorted(hints, key=lambda x: -x.score):
        s = h.subject_type or "?"
        o = h.object_type or "?"
        lines.append(
            f"  - {h.prefixed_name or h.property_iri} connects {s} → {o}. "
            f"Reason: {h.reason} (score={h.score:.2f})"
        )
    return "\n".join(lines)
