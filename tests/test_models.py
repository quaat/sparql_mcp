"""Tests for the IR Pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from graph_mcp.models import (
    AskPlan,
    BinaryExpr,
    BindPattern,
    ConstructPlan,
    Iri,
    LiteralValue,
    Prefix,
    PrefixedName,
    Projection,
    SelectPlan,
    TriplePattern,
    ValuesPattern,
    Var,
)


def test_var_must_match_regex() -> None:
    Var(name="x")  # ok
    with pytest.raises(ValidationError):
        Var(name="1bad")
    with pytest.raises(ValidationError):
        Var(name="bad-name")


def test_iri_rejects_relative() -> None:
    Iri(value="http://example.org/x")  # ok
    with pytest.raises(ValidationError):
        Iri(value="not-an-iri")
    with pytest.raises(ValidationError):
        Iri(value="<bad>")


def test_literal_cannot_have_both_lang_and_datatype() -> None:
    LiteralValue(value="hi", lang="en")
    LiteralValue(value="42", datatype="http://www.w3.org/2001/XMLSchema#integer")
    with pytest.raises(ValidationError):
        LiteralValue(
            value="hi",
            lang="en",
            datatype="http://www.w3.org/2001/XMLSchema#string",
        )


def test_prefixed_name_validation() -> None:
    PrefixedName(prefix="ex", local="foo")
    with pytest.raises(ValidationError):
        PrefixedName(prefix="bad prefix", local="foo")


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        Var.model_validate({"kind": "var", "name": "x", "extra": 1})


def test_select_plan_basic() -> None:
    plan = SelectPlan(
        prefixes=[Prefix(prefix="ex", iri="http://example.org/")],
        projection=[Projection(var=Var(name="p"))],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=PrefixedName(prefix="ex", local="worksFor"),
                object=PrefixedName(prefix="ex", local="Acme"),
            )
        ],
    )
    assert plan.kind == "select"
    assert plan.projection[0].output_name == "p"


def test_projection_must_have_var_xor_expression() -> None:
    with pytest.raises(ValidationError):
        Projection()
    with pytest.raises(ValidationError):
        Projection(var=Var(name="x"), expression=Var(name="y"))


def test_projection_with_expression_requires_alias() -> None:
    with pytest.raises(ValidationError):
        Projection(expression=Var(name="x"))
    Projection(expression=Var(name="x"), alias=Var(name="y"))  # ok


def test_construct_template_must_be_non_empty() -> None:
    with pytest.raises(ValidationError):
        ConstructPlan(template=[])


def test_values_arity_check() -> None:
    with pytest.raises(ValidationError):
        ValuesPattern(
            variables=[Var(name="a"), Var(name="b")],
            rows=[[Iri(value="http://example.org/x")]],
        )


def test_bind_pattern_round_trip() -> None:
    bp = BindPattern(
        expression=BinaryExpr(op="+", left=LiteralValue(value=1), right=LiteralValue(value=2)),
        var=Var(name="sum"),
    )
    assert bp.var.name == "sum"


def test_ask_plan() -> None:
    AskPlan(where=[])  # ok
