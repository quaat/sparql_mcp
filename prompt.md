# Claude Code Implementation Prompt: Production-Ready MCP Server for SPARQL Query Planning, Validation, Rendering, Execution, and LLM Plan Evaluation

You are Claude Code acting as a senior production engineer, Python architect, RDF/SPARQL specialist, security reviewer, and test engineer.

Build a production-quality Python repository implementing an MCP server for querying RDF graph databases through a safe, validated, structured query-plan intermediate representation instead of direct free-form SPARQL generation.

The core design principle is:

> The LLM plans. The MCP server validates, compiles, executes, and explains.

Do not build a system where the primary path is natural-language question → raw SPARQL string → execution. Raw SPARQL may exist only as a gated expert/debug feature. The primary path must be natural-language question → strict `QueryPlan` IR → validation → deterministic SPARQL rendering → execution.

The repository must include both:

1. A production-oriented MCP server exposing tools/resources/prompts for schema discovery, query-plan validation, SPARQL rendering, and graph querying.
2. A separate agentic AI/evaluation package, preferably using PydanticAI unless there is a clearly superior current option, that generates strict `QueryPlan` outputs and evaluates plan quality against a golden benchmark suite.

Before coding, inspect current official documentation for:
- MCP Python SDK / FastMCP.
- PydanticAI structured outputs and agents.
- SPARQL 1.1 Query Language, especially graph patterns, filters, property paths, aggregates, subqueries, named graphs, and security considerations.
- The selected RDF/SPARQL client library or in-memory graph engine.

Then implement.

---

## 1. Non-Negotiable Architecture

The repository must be organized around these layers:

```text
User question
  ↓
LLM planner / evaluation agent
  ↓
Strict QueryPlan IR
  ↓
QueryPlan validator
  ↓
Deterministic SPARQL renderer
  ↓
Read-only SPARQL executor
  ↓
Structured result response
````

The MCP server must not rely on a hidden server-side LLM to silently create executable SPARQL.

A separate agentic planner may exist for testing and evaluation, but its output must be strict structured data, never executable raw SPARQL by default.

---

## 2. Recommended Technology Stack

Use Python.

Use:

* `mcp` / MCP Python SDK for the server.
* Pydantic v2 for strict models.
* PydanticAI for the plan-generation evaluation agent unless documentation or compatibility strongly suggests a better current framework.
* `pytest` for tests.
* `ruff` for linting/formatting.
* `mypy` or `pyright` for static typing.
* `httpx` for HTTP requests if querying remote SPARQL endpoints.
* A local/in-memory RDF/SPARQL engine for tests, such as `rdflib` or `pyoxigraph`.
* `structlog` or standard structured logging.
* `typer` or `argparse` for local CLI utilities.
* `uv` for project/dependency management if appropriate.

Create:

* `pyproject.toml`
* `README.md`
* `.env.example`
* `Makefile` or equivalent task runner
* CI-compatible test commands
* Clear package structure under `src/`

Do not leave the repository as loose scripts.

---

## 3. Repository Structure

Create a structure similar to:

```text
.
├── README.md
├── pyproject.toml
├── .env.example
├── Makefile
├── src/
│   └── graph_mcp/
│       ├── __init__.py
│       ├── server.py
│       ├── config.py
│       ├── logging.py
│       ├── models/
│       │   ├── __init__.py
│       │   ├── iri.py
│       │   ├── literals.py
│       │   ├── expressions.py
│       │   ├── patterns.py
│       │   ├── query_plan.py
│       │   ├── results.py
│       │   └── validation.py
│       ├── compiler/
│       │   ├── __init__.py
│       │   ├── validator.py
│       │   ├── renderer.py
│       │   ├── escaping.py
│       │   └── errors.py
│       ├── graph/
│       │   ├── __init__.py
│       │   ├── endpoint.py
│       │   ├── schema_discovery.py
│       │   ├── term_resolver.py
│       │   └── result_normalizer.py
│       ├── mcp_tools/
│       │   ├── __init__.py
│       │   ├── resources.py
│       │   ├── tools.py
│       │   └── prompts.py
│       └── security/
│           ├── __init__.py
│           └── policy.py
├── evals/
│   ├── __init__.py
│   ├── agent.py
│   ├── models.py
│   ├── metrics.py
│   ├── runner.py
│   ├── golden_cases.yaml
│   └── sample_graph.ttl
└── tests/
    ├── test_models.py
    ├── test_validator.py
    ├── test_renderer.py
    ├── test_security_policy.py
    ├── test_term_resolver.py
    ├── test_mcp_tools.py
    ├── test_evals.py
    └── fixtures/
        └── sample_graph.ttl
```

Adjust only if there is a strong reason.

---

## 4. QueryPlan IR Requirements

Design a strict Pydantic model hierarchy representing a safe, expressive subset of SPARQL 1.1.

The IR must be expressive enough for complex nested and filtered questions.

Support at minimum:

### Query forms

* `SELECT`
* `ASK`
* `CONSTRUCT`, optional but useful
* Explicitly reject `DESCRIBE` by default unless implemented safely
* Explicitly reject SPARQL Update entirely

### Pattern types

Support:

* Basic triple patterns
* Group patterns
* Optional patterns
* Union patterns
* Minus patterns
* `FILTER EXISTS`
* `FILTER NOT EXISTS`
* `GRAPH` patterns for named graphs
* `VALUES`
* `BIND`
* Subquery patterns
* Property path patterns

### Expressions

Support a safe expression AST for:

* Boolean operators: `and`, `or`, `not`
* Comparisons: `=`, `!=`, `<`, `<=`, `>`, `>=`
* Membership: `in`, `not_in`
* Arithmetic where reasonable
* String functions:

  * `str`
  * `lcase`
  * `ucase`
  * `contains`
  * `strstarts`
  * `strends`
  * `regex`
* RDF term functions:

  * `bound`
  * `isIRI`
  * `isBlank`
  * `isLiteral`
  * `datatype`
  * `lang`
  * `langMatches`
* Date/time accessors where useful:

  * `year`
  * `month`
  * `day`
  * `now`
* Aggregate expressions:

  * `count`
  * `sum`
  * `avg`
  * `min`
  * `max`
  * `sample`
  * `group_concat`

### Solution modifiers

Support:

* `distinct`
* `order_by`
* `limit`
* `offset`
* `group_by`
* `having`

### Prefixes and IRIs

Implement strict IRI handling.

Never string-concatenate user text into SPARQL.

Use explicit model types for:

* Variables
* Prefixed names
* Absolute IRIs
* Literals
* Language-tagged literals
* Typed literals

Example conceptual models:

```python
class Var(BaseModel):
    kind: Literal["var"] = "var"
    name: str

class Iri(BaseModel):
    kind: Literal["iri"] = "iri"
    value: AnyUrl | str

class PrefixedName(BaseModel):
    kind: Literal["prefixed_name"] = "prefixed_name"
    prefix: str
    local: str

class LiteralValue(BaseModel):
    kind: Literal["literal"] = "literal"
    value: str | int | float | bool
    datatype: str | None = None
    lang: str | None = None
```

Use discriminated unions wherever possible.

Pydantic models must use strict validation and forbid extra fields.

---

## 5. Validator Requirements

Implement a deterministic `QueryPlanValidator`.

It must return structured validation results:

```python
class ValidationIssue(BaseModel):
    severity: Literal["error", "warning"]
    code: str
    message: str
    path: list[str | int] = []
    hint: str | None = None
```

```python
class ValidationResult(BaseModel):
    ok: bool
    issues: list[ValidationIssue]
```

The validator must check at least:

### General safety

* Only allowed query forms.
* No SPARQL Update.
* No raw SPARQL unless debug mode is explicitly enabled.
* Default `LIMIT` is applied to `SELECT` queries when not provided.
* Maximum `LIMIT` enforced.
* Maximum query depth enforced.
* Maximum number of triple patterns enforced.
* Timeout policy available at executor level.
* Result-size limit enforced.

### Dataset / graph safety

* Named graphs must be in an allowlist when an allowlist is configured.
* `SERVICE` must be unsupported or explicitly allowlisted.
* `FROM` and `FROM NAMED` must not be free-form user-controlled features in the initial implementation.

### Variables

* Variable names must match a safe regex.
* Projected variables must be bound or intentionally aggregate expressions.
* Variables used in filters must be in scope.
* Variables used outside subqueries must be projected by the subquery.
* `BIND` must not rebind an already-bound variable in the same scope.

### Aggregates

* Aggregate expressions must obey projection restrictions.
* Non-aggregated projected variables must appear in `GROUP BY`.
* `HAVING` must reference grouped or aggregate expressions only.
* Aggregate aliases must not collide with existing variables.

### Optional/filter semantics

* Warn when a `FILTER` references variables that are only bound inside an `OPTIONAL` unless the filter is inside the optional or explicitly uses `bound`.
* Warn when an optional pattern is likely converted to a required pattern by filter placement.

### Property paths

* Reject unbounded property paths unless enabled.
* Enforce maximum complexity for property path AST.
* Allow only known predicates or allowlisted path components.

### Expressions and literals

* Reject unknown functions.
* Validate regex flags.
* Validate datatype IRIs where possible.
* Warn on comparisons likely to be datatype-sensitive.

---

## 6. SPARQL Renderer Requirements

Implement a deterministic renderer:

```python
class SparqlRenderer:
    def render(self, plan: QueryPlan) -> RenderedQuery:
        ...
```

```python
class RenderedQuery(BaseModel):
    sparql: str
    warnings: list[ValidationIssue] = []
    query_type: Literal["select", "ask", "construct"]
    projected_variables: list[str] = []
```

Renderer requirements:

* Output stable, canonical SPARQL formatting.
* Sort prefixes consistently.
* Escape string literals correctly.
* Render IRIs safely.
* Never concatenate untrusted text directly.
* Use indentation for nested patterns.
* Add default `LIMIT` to `SELECT` queries when missing, through the validator or normalization layer.
* Produce readable SPARQL suitable for logs and debugging.
* Include comments only if explicitly configured; default should be no comments.

The renderer must support all IR features listed above.

---

## 7. SPARQL Executor Requirements

Implement a read-only executor abstraction:

```python
class GraphEndpoint(Protocol):
    async def query(self, sparql: str, timeout_ms: int, max_rows: int) -> QueryResult:
        ...
```

Provide at least:

1. A remote HTTP SPARQL endpoint implementation.
2. A local test/in-memory implementation.

The executor must:

* Support timeout.
* Enforce max rows.
* Normalize SPARQL JSON results into typed Python/Pydantic models.
* Return structured errors.
* Avoid logging secrets.
* Include query duration and row count metadata.
* Be mockable for tests.

---

## 8. Schema Discovery and Term Resolution

Implement resources/tools that expose schema context to the LLM.

Schema discovery should provide:

* Prefix map.
* Known classes.
* Known properties.
* Property domain/range when available.
* Named graph list when available.
* Common labels and aliases.
* Example query plans.

Term resolver:

```python
resolve_terms(
    mentions: list[str],
    expected_kinds: list[Literal["class", "property", "individual", "graph"]] | None = None,
    limit: int = 10
) -> TermResolutionResult
```

It should return ranked candidates:

```python
class TermCandidate(BaseModel):
    mention: str
    iri: str
    prefixed_name: str | None
    kind: Literal["class", "property", "individual", "graph", "unknown"]
    label: str | None
    score: float
    explanation: str
```

Initial implementation may use deterministic lexical matching over labels, local names, aliases, and configured prefixes. Keep it extensible for embeddings later.

---

## 9. MCP Server Requirements

Implement an MCP server exposing resources, prompts, and tools.

Use the current MCP Python SDK idioms.

### Resources

Expose resources similar to:

* `graph://schema/prefixes`
* `graph://schema/classes`
* `graph://schema/properties`
* `graph://schema/named-graphs`
* `graph://schema/examples`
* `graph://policy/security`
* `graph://query-plan/schema`

### Tools

Expose tools similar to:

#### `resolve_terms`

Input:

```json
{
  "mentions": ["person", "works for"],
  "expected_kinds": ["class", "property"],
  "limit": 10
}
```

Output: structured term candidates.

#### `validate_query_plan`

Input:

```json
{
  "plan": {}
}
```

Output: `ValidationResult`.

#### `render_sparql`

Input:

```json
{
  "plan": {}
}
```

Output: `RenderedQuery`.

Must validate before rendering.

#### `query_graph`

Input:

```json
{
  "plan": {},
  "max_rows": 100,
  "timeout_ms": 5000,
  "dry_run": false
}
```

Behavior:

* Validate plan.
* Render SPARQL.
* If `dry_run`, return rendered query and validation result without execution.
* If not `dry_run`, execute.
* Return normalized results and metadata.

#### `explain_query_plan`

Input:

```json
{
  "plan": {}
}
```

Output:

* Human-readable explanation.
* Variables selected.
* Graph patterns used.
* Filters applied.
* Potential warnings.

#### `execute_sparql_raw`

This must be disabled by default.

Only enable if `GRAPH_MCP_ENABLE_RAW_SPARQL=true`.

Even then:

* Read-only only.
* Reject SPARQL Update.
* Enforce timeout and row limit.
* Reject `SERVICE` unless explicitly allowlisted.
* Clearly mark result as raw/expert mode.

### Prompts

Expose at least one MCP prompt:

#### `build_query_plan`

This prompt should instruct the host LLM to:

1. Inspect schema resources.
2. Resolve terms using `resolve_terms`.
3. Produce a strict `QueryPlan`, not SPARQL.
4. Call `validate_query_plan`.
5. Repair validation errors if needed.
6. Call `query_graph` only after validation succeeds.
7. Use `dry_run` for complex queries first.
8. Explain assumptions and ambiguity.

The prompt must explicitly warn against:

* inventing prefixes, classes, properties, or named graphs;
* writing raw SPARQL;
* using unsupported SPARQL features;
* executing broad queries without a limit;
* using unbounded property paths without justification.

---

## 10. Separate Agentic AI Planner / Evaluation System

Create an `evals/` package that can evaluate how well an LLM generates `QueryPlan` objects.

Use PydanticAI if suitable.

The planner agent must have:

* A strict structured output type.
* Access to schema context.
* Optional tool access to deterministic term resolution.
* No permission to execute raw SPARQL.
* Clear instructions to produce `QueryPlan` only.

### Planner output model

Create a strict model similar to:

```python
class PlanGenerationOutput(BaseModel):
    question: str
    assumptions: list[str]
    resolved_terms: list[TermCandidate]
    plan: QueryPlan
    confidence: float
    needs_clarification: bool
    clarification_question: str | None = None
```

The final planner output must validate with Pydantic.

No free-form JSON blobs.

### Planner instructions

The planner system prompt must say:

* You transform natural-language questions into safe `QueryPlan` IR.
* Do not output raw SPARQL.
* Use only schema terms supplied in context or returned by tools.
* Prefer precise filters over broad graph scans.
* Use `OPTIONAL` only for optional information, not required constraints.
* Place filters inside `OPTIONAL` when the filter should only constrain optional bindings.
* Use `FILTER NOT EXISTS` for absence-of-pattern semantics unless `MINUS` is specifically more appropriate.
* Use subqueries for top-N, grouped aggregation, and nested constraints where needed.
* Use aggregates only with valid grouping.
* Always include a reasonable limit for exploratory `SELECT` queries.
* Set `needs_clarification=true` only when the question cannot be safely mapped to known schema terms.

### Evaluation runner

Implement a CLI:

```bash
uv run python -m evals.runner --model <model-name> --cases evals/golden_cases.yaml
```

The runner must:

1. Load benchmark cases.
2. Run the planner.
3. Validate the returned `QueryPlan`.
4. Render SPARQL.
5. Optionally execute against the sample graph.
6. Compare outputs to expected plan features and expected result rows.
7. Produce a JSON and markdown report.

### Golden cases

Create at least 20 benchmark cases covering:

1. Simple class lookup.
2. Label search with language filter.
3. Required relationship.
4. Optional property.
5. Optional with correctly scoped filter.
6. Union of alternative relationships.
7. Negation using `FILTER NOT EXISTS`.
8. Difference using `MINUS`.
9. Property path one-or-more.
10. Bounded property path.
11. `VALUES` for a list of entities.
12. `BIND` for computed value.
13. Count grouped by class/property.
14. `HAVING` filter.
15. Top-N aggregate using subquery.
16. Named graph query.
17. Date filter.
18. Datatype-sensitive numeric comparison.
19. Ambiguous term requiring low confidence or clarification.
20. Unsafe request that must be rejected or constrained.

Each case should include:

```yaml
- id: "case_001"
  question: "Which people work for Acme?"
  schema_context: "default"
  expected:
    required_patterns:
      - "triple"
    required_terms:
      - "ex:Person"
      - "ex:worksFor"
      - "ex:Acme"
    forbidden_features:
      - "raw_sparql"
      - "service"
    result_expectation:
      min_rows: 1
```

Do not require exact textual SPARQL matching as the primary metric. Prefer structural comparison of the `QueryPlan` and execution result checks.

### Metrics

Implement metrics such as:

* `valid_plan_rate`
* `render_success_rate`
* `execution_success_rate`
* `required_feature_recall`
* `forbidden_feature_violation_rate`
* `term_resolution_accuracy`
* `result_accuracy`
* `safety_violation_count`

The report must show failures with:

* question
* generated plan
* validation issues
* rendered SPARQL if available
* expected features
* actual features
* result mismatch details

---

## 11. Testing Requirements

Write comprehensive tests.

Minimum required test categories:

### Model tests

* Valid plans parse.
* Invalid extra fields are rejected.
* Invalid variable names are rejected.
* Invalid literal forms are rejected.
* Discriminated unions work.

### Validator tests

Cover:

* projected variable not bound
* filter variable not in scope
* subquery variable not projected
* invalid aggregate projection
* invalid `HAVING`
* `BIND` rebinding
* unsafe named graph
* unsupported `SERVICE`
* raw SPARQL disabled
* limit normalization
* property path complexity limit
* optional/filter warning

### Renderer tests

Use snapshot-like assertions for:

* simple select
* optional
* union
* negation
* property path
* aggregation
* subquery
* named graph
* values
* bind
* escaped literals

The renderer output should be stable.

### Executor tests

* local sample graph query works
* timeout behavior is handled
* max rows enforced
* SPARQL JSON results normalized
* endpoint errors become structured errors

### MCP tests

* tools accept valid inputs
* tools reject invalid inputs
* resources return expected schema
* prompt exists and contains the core instructions
* raw SPARQL tool is disabled by default

### Eval tests

* golden cases load
* planner output model validates
* metric calculations work
* report generation works
* at least one deterministic/mock planner test passes without calling an external LLM

All tests must be runnable without paid external services. LLM-backed tests must be optional and skipped unless API keys are configured.

---

## 12. Security Requirements

Implement `security/policy.py`.

Configuration must support:

```env
GRAPH_MCP_ENDPOINT_URL=
GRAPH_MCP_DEFAULT_LIMIT=100
GRAPH_MCP_MAX_LIMIT=1000
GRAPH_MCP_TIMEOUT_MS=5000
GRAPH_MCP_ALLOWED_GRAPHS=
GRAPH_MCP_ALLOWED_SERVICE_ENDPOINTS=
GRAPH_MCP_ENABLE_RAW_SPARQL=false
GRAPH_MCP_LOG_LEVEL=INFO
```

Security policy must enforce:

* read-only by default;
* no SPARQL Update;
* no arbitrary `SERVICE`;
* no arbitrary `FROM` / `FROM NAMED` in v1;
* named graph allowlist;
* max query depth;
* max triple patterns;
* max result rows;
* timeout;
* sanitized logs;
* no secrets in exceptions;
* no hidden server-side LLM calls during query execution.

Raw SPARQL expert mode must be clearly isolated and tested.

---

## 13. Documentation Requirements

Write a strong `README.md` explaining:

* What the server does.
* Why it uses `QueryPlan` IR instead of direct raw SPARQL.
* Installation.
* Configuration.
* Running the MCP server.
* Running tests.
* Running evals.
* How to connect from Claude Code or an MCP client.
* Example natural-language question → QueryPlan → rendered SPARQL → result.
* Security model.
* Limitations.
* Extension points.

Also document:

* How to add a new expression function.
* How to add a new pattern type.
* How to add schema-specific aliases.
* How to add new golden eval cases.
* How to enable raw SPARQL expert mode safely.

---

## 14. Quality Bar

The implementation must be production-oriented.

Requirements:

* Type annotations everywhere.
* Pydantic strict validation.
* No global mutable state unless justified.
* Clean separation of concerns.
* Dependency injection for endpoint, schema provider, and policy.
* Async I/O for endpoint calls where appropriate.
* Deterministic rendering.
* Structured errors.
* Structured logs.
* Tests for edge cases.
* No broad `except Exception` without wrapping and preserving diagnostic context.
* No hardcoded secrets.
* No generated binary blobs.
* No placeholder TODOs in core logic.
* No fake tests that assert only that functions exist.
* No hidden network calls in unit tests.
* No external paid LLM calls unless explicitly enabled.

Use simple, readable code over clever abstractions.

---

## 15. Implementation Steps

Proceed in this order:

1. Create project skeleton and dependency config.
2. Implement core Pydantic models.
3. Implement security policy.
4. Implement validator.
5. Implement renderer.
6. Implement local test graph executor.
7. Implement remote endpoint executor.
8. Implement schema discovery and term resolver.
9. Implement MCP resources/tools/prompts.
10. Implement eval agent models and runner.
11. Add sample graph and golden cases.
12. Write tests.
13. Write documentation.
14. Run formatting, linting, typing, and tests.
15. Fix failures.
16. Produce a final implementation summary.

---

## 16. Acceptance Criteria

The task is complete only when:

* `README.md` is complete.
* `pyproject.toml` is valid.
* The MCP server starts locally.
* `query_graph` can validate, render, and execute a sample `QueryPlan`.
* `validate_query_plan` catches semantic errors.
* `render_sparql` produces deterministic SPARQL.
* The eval runner can run against mock or local planner mode without external API keys.
* LLM-backed eval mode is available but optional.
* Tests pass.
* Linting passes.
* Type checks pass, or any remaining type-check limitations are explicitly documented.
* The security model is implemented and tested.
* Raw SPARQL execution is disabled by default.

---

## 17. Final Response Required from Claude Code

After implementation, report:

1. Files created.
2. Key architectural decisions.
3. How to run the server.
4. How to run tests.
5. How to run evals.
6. Known limitations.
7. Any deviations from this prompt and why.
8. Exact commands run and whether they passed.

Do not claim something passes unless you actually ran it.

If a command could not be run, explain why.

---

## 18. Important Design Guidance

Do not let the project devolve into a thin wrapper around arbitrary SPARQL strings.

The important artifact is the `QueryPlan` IR and its validator/compiler.

The LLM planner is allowed to be imperfect. The MCP server must be robust against imperfect plans.

The generated code should make it easy for a later reviewer to answer:

* Is this safe?
* Is this testable?
* Is the query semantics inspectable?
* Can we add support for more SPARQL features without rewriting everything?
* Can we measure whether LLM-generated plans are getting better?

````

---

## Optional addendum to give Claude Code after the first implementation

Use this shorter follow-up prompt once Claude Code has produced the repository:

```markdown
Review your own implementation against the original specification.

Produce a gap analysis with:

1. Fully implemented requirements.
2. Partially implemented requirements.
3. Missing requirements.
4. Security concerns.
5. Test coverage gaps.
6. Refactoring recommendations.
7. A prioritized fix list.

Then implement the highest-priority fixes until tests, linting, and type checks pass.

Do not change the architecture away from:
LLM → QueryPlan IR → validator → renderer → executor.
