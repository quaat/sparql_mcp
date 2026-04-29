"""Documentation-coverage check for the docs-site.

Verifies, statically:

* every ``GRAPH_MCP_*`` variable in ``.env.example`` is mentioned in
  ``docs-site/docs/reference/configuration-reference.md``;
* every MCP tool registered in ``src/graph_mcp/server.py`` is mentioned
  in ``docs-site/docs/reference/tools-reference.md``;
* every MCP resource URI registered in ``src/graph_mcp/server.py`` is
  mentioned in ``docs-site/docs/reference/resources-reference.md``;
* no unauthorized placeholder strings remain (``TODO``, ``TBD``,
  ``FIXME``) outside the explicit "Known limitations" sections;
* (optionally) the docs-site build output exists at
  ``docs-site/build`` — only enforced when ``--require-build`` is passed.

Run with no args to print problems and exit non-zero on failure. Use
``--require-build`` in CI after building the site.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = ROOT / ".env.example"
SERVER_PY = ROOT / "src" / "graph_mcp" / "server.py"
DOCS_SITE = ROOT / "docs-site"
DOCS_DIR = DOCS_SITE / "docs"
CONFIG_REF = DOCS_DIR / "reference" / "configuration-reference.md"
TOOLS_REF = DOCS_DIR / "reference" / "tools-reference.md"
RESOURCES_REF = DOCS_DIR / "reference" / "resources-reference.md"
BUILD_DIR = DOCS_SITE / "build"

# Placeholder strings that fail the check unless they appear in a
# "Known limitations" / "Roadmap" / "Future work" section.
PLACEHOLDER_RE = re.compile(r"\b(TODO|TBD|FIXME)\b")
ALLOWED_HEADERS = ("Known limitations", "Future work", "Roadmap", "Open questions")


def fail(msg: str, *, problems: list[str]) -> None:
    problems.append(msg)


def env_vars() -> list[str]:
    out: list[str] = []
    for line in ENV_EXAMPLE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, _ = line.partition("=")
        if name.startswith("GRAPH_MCP_"):
            out.append(name)
    return out


def server_decorations(decorator: str) -> list[str]:
    """Find names registered with ``@mcp.<decorator>(...)`` via simple regex.

    We do not import the package — the check should run in CI even
    before extras are installed.
    """
    src = SERVER_PY.read_text()
    if decorator == "tool":
        # `@mcp.tool()` followed by a Python function definition.
        return re.findall(
            r"@mcp\.tool\(\)\s+(?:async\s+)?def\s+([A-Za-z_][A-Za-z_0-9]*)",
            src,
        )
    if decorator == "resource":
        return re.findall(r'@mcp\.resource\("([^"]+)"\)', src)
    raise ValueError(f"unsupported decorator: {decorator}")


def doc_files(prefix: Path) -> list[Path]:
    return sorted(p for p in prefix.rglob("*.md") if p.is_file())


def check_placeholders(problems: list[str]) -> None:
    for path in doc_files(DOCS_DIR):
        text = path.read_text()
        # Walk sections so we can grant placeholder amnesty inside
        # "Known limitations" etc.
        in_allowed = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                in_allowed = any(h.lower() in stripped.lower() for h in ALLOWED_HEADERS)
                continue
            if in_allowed:
                continue
            m = PLACEHOLDER_RE.search(line)
            if m:
                fail(
                    f"placeholder {m.group(0)!r} in {path.relative_to(ROOT)} "
                    f"(line: {stripped[:120]!r}); "
                    "move it under a 'Known limitations' / 'Roadmap' / "
                    "'Future work' / 'Open questions' heading or remove it.",
                    problems=problems,
                )


def check_env_coverage(problems: list[str]) -> None:
    if not CONFIG_REF.exists():
        fail(
            f"missing reference page {CONFIG_REF.relative_to(ROOT)}",
            problems=problems,
        )
        return
    text = CONFIG_REF.read_text()
    for var in env_vars():
        if var not in text:
            fail(
                f"{var} is in .env.example but not mentioned in {CONFIG_REF.relative_to(ROOT)}",
                problems=problems,
            )


def check_tool_coverage(problems: list[str]) -> None:
    if not TOOLS_REF.exists():
        fail(
            f"missing reference page {TOOLS_REF.relative_to(ROOT)}",
            problems=problems,
        )
        return
    text = TOOLS_REF.read_text()
    tools = server_decorations("tool")
    if not tools:
        fail(
            "no @mcp.tool() decorators found in server.py — refusing to claim full tool coverage",
            problems=problems,
        )
        return
    for t in tools:
        if t not in text:
            fail(
                f"MCP tool {t!r} is registered in server.py but not "
                f"mentioned in {TOOLS_REF.relative_to(ROOT)}",
                problems=problems,
            )


def check_resource_coverage(problems: list[str]) -> None:
    if not RESOURCES_REF.exists():
        fail(
            f"missing reference page {RESOURCES_REF.relative_to(ROOT)}",
            problems=problems,
        )
        return
    text = RESOURCES_REF.read_text()
    resources = server_decorations("resource")
    if not resources:
        fail(
            "no @mcp.resource(...) decorators found in server.py",
            problems=problems,
        )
        return
    for r in resources:
        if r not in text:
            fail(
                f"MCP resource {r!r} is registered in server.py but not "
                f"mentioned in {RESOURCES_REF.relative_to(ROOT)}",
                problems=problems,
            )


def check_build(problems: list[str]) -> None:
    if not BUILD_DIR.is_dir():
        fail(
            f"build output {BUILD_DIR.relative_to(ROOT)} not found; run "
            "'cd docs-site && npm run build' first",
            problems=problems,
        )
        return
    index = BUILD_DIR / "index.html"
    if not index.is_file():
        fail(
            f"build output is missing index.html at {index.relative_to(ROOT)}",
            problems=problems,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--require-build",
        action="store_true",
        help="Also assert that docs-site/build/index.html exists.",
    )
    args = parser.parse_args(argv)

    problems: list[str] = []
    check_env_coverage(problems)
    check_tool_coverage(problems)
    check_resource_coverage(problems)
    check_placeholders(problems)
    if args.require_build:
        check_build(problems)

    if problems:
        sys.stderr.write(
            "[check_docs] documentation coverage problems:\n  - " + "\n  - ".join(problems) + "\n"
        )
        return 1
    sys.stderr.write("[check_docs] all coverage checks passed\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
