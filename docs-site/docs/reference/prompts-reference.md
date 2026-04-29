---
id: prompts-reference
title: MCP prompts reference
sidebar_position: 4
description: Every MCP prompt registered by graph-mcp, with arguments, intended workflow, and limitations.
---

# MCP prompts reference

Prompts are a third MCP surface, distinct from tools and resources. A
prompt is a host-renderable template the LLM can opt into; the host
substitutes user-provided arguments and feeds the result to the model.
Calling a prompt does **not** execute server-side code beyond template
rendering — the server only returns the rendered text.

The list below is auto-derived from `src/graph_mcp/server.py`.

<!-- BEGIN: managed:prompts-table -->
| Prompt | Anchor |
| --- | --- |
| `build_query_plan` | [Details](#build-query-plan) |
<!-- END: managed:prompts-table -->

## build-query-plan

**Purpose:** steer the host LLM into the safe `QueryPlan` workflow
instead of letting it write raw SPARQL.

**Arguments:**

| Name | Type | Required | Description |
| --- | --- | --- | --- |
| `question` | `string` | yes | The user's natural-language question; the LLM will plan a query that answers it. |

**When the host should use it.** Whenever the user asks an
open-ended question that needs the graph, especially before the host
has decided which tools to call. Once the LLM is in the workflow, the
host should invoke tools normally.

**Workflow the prompt instructs.** The rendered text guides the LLM
through these steps in order:

1. **Inspect schema resources.** Read `graph://schema/prefixes`,
   `graph://schema/classes`, `graph://schema/properties`,
   `graph://schema/named-graphs`, and (for entity mentions)
   `graph://schema/individuals`. Read `graph://query-plan/schema`
   to learn the IR.
2. **Resolve terms.** For every natural-language entity / relation
   in the question, call `resolve_terms` to get ranked candidate
   IRIs. Do not invent IRIs.
3. **Emit a `QueryPlan`, not raw SPARQL.** Use precise filters,
   `OPTIONAL` for genuinely optional information (with `FILTER`
   inside the OPTIONAL when appropriate), `FILTER NOT EXISTS` for
   absence-of-pattern semantics, subqueries for top-N and grouped
   aggregation, and a sensible `LIMIT` for exploratory selects.
4. **Validate.** Call `validate_query_plan`. If errors are present,
   repair the plan from the structured `ValidationIssue` list and
   re-validate.
5. **Inspect.** For complex queries, call `render_sparql` (or
   `query_graph` with `dry_run=true`) to see the SPARQL the server
   would send.
6. **Execute.** Call `query_graph` with a small `max_rows`. Surface
   any assumptions and the IRIs chosen in the final answer.

**Safety instructions in the prompt.** The rendered text also
includes hard prohibitions:

- never write raw SPARQL strings;
- never use `DESCRIBE`, SPARQL Update, or arbitrary `SERVICE`;
- never use unbounded property paths without explicit justification;
- never execute broad queries without a `LIMIT`.

These are advisory text inside the prompt, not enforcement. The
**enforcement** still happens in the validator and the security
policy — the prompt is a guidance layer.

**Limitations.**

- Prompts are an MCP capability the host must surface. If the host
  does not expose the prompt UI, the workflow guidance never reaches
  the model. Tools and resources are still callable directly.
- The prompt is a template, not a tool — invoking it does not call
  the validator, render SPARQL, or execute anything.
- The prompt does not invoke `resolve_terms` itself. It instructs the
  LLM to do so. Hosts that want tool-backed term resolution should
  ensure the LLM exercises `resolve_terms` before emitting a plan.

## See also

- [User guide → MCP tools](/users/mcp-tools/)
- [User guide → MCP resources](/users/mcp-resources/)
- [Tools reference](/reference/tools-reference/)
- [Resources reference](/reference/resources-reference/)
- The prompt template lives in `src/graph_mcp/mcp_tools/prompts.py`.
