"""LLM plan-quality evaluation package.

The evaluation harness has three pieces:

- :mod:`evals.models` — typed I/O for the planner.
- :mod:`evals.agent` — PydanticAI agent (optional) plus a deterministic mock
  planner that requires no API key.
- :mod:`evals.runner` — CLI that runs the planner across golden cases and
  produces a JSON+markdown report.
"""
