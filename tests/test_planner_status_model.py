"""Tests for the discriminated PlanGenerationOutput status model (§2)."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from evals.models import (
    ClarificationOutput,
    PlanGenerationOutput,
    PlannedOutput,
    RefusedOutput,
    is_clarification,
    is_planned,
    is_refused,
)
from graph_mcp.models import (
    Prefix,
    PrefixedName,
    Projection,
    SelectPlan,
    TriplePattern,
    Var,
)

EX = Prefix(prefix="ex", iri="http://example.org/")


def _good_plan() -> SelectPlan:
    return SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="p"))],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=PrefixedName(prefix="ex", local="knows"),
                object=Var(name="q"),
            )
        ],
    )


def test_planned_output_requires_plan() -> None:
    plan = _good_plan()
    out = PlannedOutput(question="?", plan=plan, confidence=0.9)
    assert out.status == "planned"
    assert is_planned(out)
    assert not is_clarification(out)
    assert not is_refused(out)


def test_planned_output_rejects_missing_plan() -> None:
    with pytest.raises(ValidationError):
        PlannedOutput(question="?", confidence=0.9)  # type: ignore[call-arg]


def test_clarification_output_does_not_require_plan() -> None:
    out = ClarificationOutput(
        question="?",
        confidence=0.1,
        clarification_question="Which one?",
    )
    assert out.status == "needs_clarification"
    assert is_clarification(out)
    assert not is_planned(out)
    # Crucially, the model has no ``plan`` field at all.
    assert "plan" not in out.model_dump()


def test_refused_output_must_not_require_plan() -> None:
    out = RefusedOutput(
        question="?",
        confidence=0.0,
        refusal_reason="destructive",
        policy_code="unsafe_destructive_request",
    )
    assert out.status == "refused"
    assert is_refused(out)
    assert not is_planned(out)
    assert "plan" not in out.model_dump()


def test_discriminated_union_picks_correct_variant() -> None:
    adapter = TypeAdapter(PlanGenerationOutput)
    parsed_planned = adapter.validate_python(
        {
            "status": "planned",
            "question": "?",
            "confidence": 0.9,
            "plan": _good_plan().model_dump(),
        }
    )
    assert isinstance(parsed_planned, PlannedOutput)

    parsed_clarif = adapter.validate_python(
        {
            "status": "needs_clarification",
            "question": "?",
            "confidence": 0.2,
            "clarification_question": "Which one?",
        }
    )
    assert isinstance(parsed_clarif, ClarificationOutput)

    parsed_refused = adapter.validate_python(
        {
            "status": "refused",
            "question": "?",
            "confidence": 0.0,
            "refusal_reason": "no",
        }
    )
    assert isinstance(parsed_refused, RefusedOutput)
