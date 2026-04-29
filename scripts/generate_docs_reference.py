"""Generate machine-derived documentation fragments for the docs site.

This script produces:

* ``docs-site/static/schema/query-plan.schema.json`` —
  the JSON Schema for the ``QueryPlan`` IR;
* a managed configuration table inside
  ``docs-site/docs/reference/configuration-reference.md``,
  derived from ``.env.example`` and ``graph_mcp.config.Settings``;
* a managed MCP-tools list inside
  ``docs-site/docs/reference/tools-reference.md``;
* a managed MCP-resources list inside
  ``docs-site/docs/reference/resources-reference.md``.

The "managed" fragments are wrapped in HTML comments so the surrounding
prose stays editable:

.. code:: text

    <!-- BEGIN: managed:config-table -->
    | ... auto-generated content ... |
    <!-- END: managed:config-table -->

Run with no arguments to (re)generate. Run with ``--check`` to fail when
the on-disk content drifts from what would be generated; the CI workflow
calls it with ``--check`` so doc drift fails the build rather than
shipping silently stale references.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = ROOT / ".env.example"
CONFIG_PY = ROOT / "src" / "graph_mcp" / "config.py"
SERVER_PY = ROOT / "src" / "graph_mcp" / "server.py"
DOCS_SITE = ROOT / "docs-site"
SCHEMA_OUT = DOCS_SITE / "static" / "schema" / "query-plan.schema.json"
CONFIG_REF = DOCS_SITE / "docs" / "reference" / "configuration-reference.md"
TOOLS_REF = DOCS_SITE / "docs" / "reference" / "tools-reference.md"
RESOURCES_REF = DOCS_SITE / "docs" / "reference" / "resources-reference.md"
PROMPTS_REF = DOCS_SITE / "docs" / "reference" / "prompts-reference.md"

# Variables whose default exposes data or relaxes the IR; flag in the
# generated configuration table so the column is not just a copy of the
# .env defaults. The list is intentionally hand-maintained — it reflects
# operator-visible risk, not Pydantic validation.
SECURITY_SENSITIVE_VARS: dict[str, str] = {
    "GRAPH_MCP_ENABLE_RAW_SPARQL": (
        "Bypasses IR-level structural checks; only enable for trusted callers."
    ),
    "GRAPH_MCP_ALLOW_UNBOUNDED_PATHS": (
        "Allows `*` / `+` paths; pair with `GRAPH_MCP_ALLOWED_PATH_PREDICATES` if enabled."
    ),
    "GRAPH_MCP_ALLOWED_GRAPHS": (
        "Empty disables the named-graph allowlist; an explicit list is recommended in production."
    ),
    "GRAPH_MCP_ALLOWED_SERVICE_ENDPOINTS": (
        "Each entry is a data-exfiltration channel; treat like a firewall rule."
    ),
    "GRAPH_MCP_ALLOWED_PATH_PREDICATES": (
        "Empty disables the predicate allowlist for property paths."
    ),
    "GRAPH_MCP_MAX_LIMIT": ("Cap on rows per query; tighter than the upstream engine's budget."),
    "GRAPH_MCP_TIMEOUT_MS": (
        "Caller-side timeout; the engine must enforce its own "
        "per-query budget for hard cancellation."
    ),
    "GRAPH_MCP_ALLOW_DEFAULT_PREFIX_OVERRIDE": (
        "Permits redefinition of `rdf`, `xsd`, etc.; rarely correct."
    ),
    "GRAPH_MCP_ENDPOINT_URL": ("Empty falls back to the local in-memory rdflib executor."),
    "GRAPH_MCP_LOCAL_GRAPH_FILE": ("Loaded once at startup; on-disk changes are not picked up."),
    "GRAPH_MCP_LOG_LEVEL": ("`DEBUG` may emit query text; keep `INFO` or higher in production."),
}


# --- Generic managed-block helpers -----------------------------------------


def _replace_managed_block(text: str, name: str, body: str) -> str:
    begin = f"<!-- BEGIN: managed:{name} -->"
    end = f"<!-- END: managed:{name} -->"
    pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), flags=re.DOTALL)
    replacement = f"{begin}\n{body.rstrip()}\n{end}"
    if not pattern.search(text):
        raise SystemExit(
            f"managed block {name!r} not found in target file. "
            "Add the BEGIN/END markers before regenerating."
        )
    return pattern.sub(replacement, text)


def _table(rows: list[list[str]], header: list[str]) -> str:
    sep = ["---"] * len(header)

    def fmt(row: list[str]) -> str:
        return "| " + " | ".join(c.replace("|", "\\|") for c in row) + " |"

    return "\n".join([fmt(header), fmt(sep), *(fmt(r) for r in rows)])


# --- .env.example parsing ---------------------------------------------------


@dataclass(frozen=True)
class EnvVar:
    name: str
    default: str
    description: str


def parse_env_example(path: Path) -> list[EnvVar]:
    """Parse ``GRAPH_MCP_*`` variables from the .env.example template.

    The format is a simple succession of comment + assignment pairs:

    .. code::

        # Description of FOO.
        GRAPH_MCP_FOO=bar

    Multiple comment lines accumulate into one description. Section
    headers like ``# --- Schema-provider configuration ---`` are
    skipped.
    """
    out: list[EnvVar] = []
    description_lines: list[str] = []
    for raw in path.read_text().splitlines():
        line = raw.rstrip()
        if not line:
            description_lines = []
            continue
        if line.startswith("#"):
            text = line.lstrip("# ").strip()
            # Skip section dividers like "--- Schema ----".
            if set(text) <= set("- "):
                description_lines = []
                continue
            description_lines.append(text)
            continue
        if "=" not in line:
            continue
        name, _, default = line.partition("=")
        if not name.startswith("GRAPH_MCP_"):
            description_lines = []
            continue
        out.append(
            EnvVar(
                name=name.strip(),
                default=default.strip(),
                description=" ".join(description_lines).strip(),
            )
        )
        description_lines = []
    return out


def render_config_table(env_vars: Iterable[EnvVar]) -> str:
    rows: list[list[str]] = []
    for v in env_vars:
        default = v.default if v.default else "_(empty)_"
        # "Required" reflects deployment-time semantics, not pydantic
        # validation: a Settings field with a default is technically
        # optional, but some are required for the server to do anything
        # useful (e.g. an endpoint URL when the schema provider is in
        # `sparql` mode).
        if v.name in {"GRAPH_MCP_ENDPOINT_URL", "GRAPH_MCP_LOCAL_GRAPH_FILE"}:
            required = "one of these two for `sparql` schema provider"
        else:
            required = "no"
        security = SECURITY_SENSITIVE_VARS.get(v.name, "")
        rows.append(
            [
                f"`{v.name}`",
                f"`{default}`",
                required,
                security,
                v.description or "_(undocumented)_",
            ]
        )
    return _table(
        rows,
        header=["Variable", "Default", "Required", "Security impact", "Description"],
    )


# --- MCP tool / resource discovery -----------------------------------------


def discover_mcp_decorations(path: Path, decorator: str) -> list[str]:
    """Return the names registered with ``@mcp.<decorator>(...)`` in a module.

    Walks the AST so we don't depend on import-time side effects.

    - For ``tool``, returns the function name (the MCP tool name).
    - For ``resource``, returns the URI passed to ``@mcp.resource("...")``.
    - For ``prompt``, returns the prompt name passed to
      ``@mcp.prompt("...")``.
    """
    tree = ast.parse(path.read_text())
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            attr = dec.func
            if not isinstance(attr, ast.Attribute) or attr.attr != decorator:
                continue
            if decorator == "tool":
                out.append(node.name)
            elif decorator in ("resource", "prompt"):
                if dec.args and isinstance(dec.args[0], ast.Constant):
                    out.append(str(dec.args[0].value))
            else:  # pragma: no cover
                raise SystemExit(f"unsupported decorator: {decorator}")
    return out


def render_tools_list(tool_names: Iterable[str]) -> str:
    rows: list[list[str]] = []
    for name in tool_names:
        rows.append(
            [
                f"`{name}`",
                f"[Details](#{name.replace('_', '-')})",
            ]
        )
    return _table(rows, header=["Tool", "Anchor"])


def render_resources_list(uris: Iterable[str]) -> str:
    rows: list[list[str]] = []
    for uri in uris:
        anchor = uri.replace("graph://", "").replace("/", "-")
        rows.append([f"`{uri}`", f"[Details](#{anchor})"])
    return _table(rows, header=["URI", "Anchor"])


def render_prompts_list(prompt_names: Iterable[str]) -> str:
    rows: list[list[str]] = []
    for name in prompt_names:
        anchor = name.replace("_", "-")
        rows.append([f"`{name}`", f"[Details](#{anchor})"])
    return _table(rows, header=["Prompt", "Anchor"])


# --- QueryPlan JSON Schema --------------------------------------------------


def render_query_plan_schema() -> str:
    # Imported lazily because the script is also useful in --check mode
    # before the package is installed.
    from pydantic import TypeAdapter

    from graph_mcp.models import QueryPlan

    adapter: TypeAdapter[QueryPlan] = TypeAdapter(QueryPlan)
    return json.dumps(adapter.json_schema(), indent=2, sort_keys=True) + "\n"


# --- Driver ----------------------------------------------------------------


@dataclass
class Plan:
    path: Path
    desired: str
    label: str

    def apply(self, *, check: bool) -> bool:
        actual = self.path.read_text() if self.path.exists() else ""
        if actual == self.desired:
            return True
        if check:
            diff = "\n".join(
                difflib.unified_diff(
                    actual.splitlines(),
                    self.desired.splitlines(),
                    fromfile=f"a/{self.path.relative_to(ROOT)}",
                    tofile=f"b/{self.path.relative_to(ROOT)}",
                    n=2,
                )
            )
            sys.stderr.write(
                f"[generate_docs_reference] {self.label} is stale: "
                f"{self.path.relative_to(ROOT)}\n{diff}\n"
            )
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.desired)
        sys.stderr.write(f"[generate_docs_reference] wrote {self.path.relative_to(ROOT)}\n")
        return True


def build_plans() -> list[Plan]:
    plans: list[Plan] = []

    # 1. QueryPlan JSON Schema.
    plans.append(
        Plan(
            path=SCHEMA_OUT,
            desired=render_query_plan_schema(),
            label="QueryPlan JSON Schema",
        )
    )

    # 2. Configuration reference table.
    env_vars = parse_env_example(ENV_EXAMPLE)
    config_block = render_config_table(env_vars)
    if CONFIG_REF.exists():
        config_text = _replace_managed_block(CONFIG_REF.read_text(), "config-table", config_block)
        plans.append(
            Plan(
                path=CONFIG_REF,
                desired=config_text,
                label="configuration table",
            )
        )

    # 3. MCP tools list.
    tool_names = discover_mcp_decorations(SERVER_PY, "tool")
    tools_block = render_tools_list(tool_names)
    if TOOLS_REF.exists():
        tools_text = _replace_managed_block(TOOLS_REF.read_text(), "tools-table", tools_block)
        plans.append(Plan(path=TOOLS_REF, desired=tools_text, label="tools list"))

    # 4. MCP resources list.
    resource_uris = discover_mcp_decorations(SERVER_PY, "resource")
    resources_block = render_resources_list(resource_uris)
    if RESOURCES_REF.exists():
        resources_text = _replace_managed_block(
            RESOURCES_REF.read_text(), "resources-table", resources_block
        )
        plans.append(
            Plan(
                path=RESOURCES_REF,
                desired=resources_text,
                label="resources list",
            )
        )

    # 5. MCP prompts list.
    prompt_names = discover_mcp_decorations(SERVER_PY, "prompt")
    prompts_block = render_prompts_list(prompt_names)
    if PROMPTS_REF.exists():
        prompts_text = _replace_managed_block(
            PROMPTS_REF.read_text(), "prompts-table", prompts_block
        )
        plans.append(
            Plan(
                path=PROMPTS_REF,
                desired=prompts_text,
                label="prompts list",
            )
        )

    return plans


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail (non-zero exit) when generated artifacts are stale.",
    )
    args = parser.parse_args(argv)

    plans = build_plans()
    ok = all(p.apply(check=args.check) for p in plans)
    if args.check and not ok:
        sys.stderr.write(
            "[generate_docs_reference] run scripts/generate_docs_reference.py to refresh.\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
