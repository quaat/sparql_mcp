.PHONY: install fmt lint typecheck test eval run all

install:
	uv pip install -e ".[dev,ai]"

fmt:
	ruff format src tests evals

lint:
	ruff check src tests evals

typecheck:
	mypy src

test:
	pytest

eval:
	python -m evals.runner --planner deterministic --cases evals/golden_cases.yaml --report-dir build/eval_report

run:
	python -m graph_mcp.server

all: lint typecheck test
