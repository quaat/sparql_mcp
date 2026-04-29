"""Documentation-coverage check for the docs-site.

Verifies, statically:

* every ``GRAPH_MCP_*`` variable in ``.env.example`` is mentioned in
  ``docs-site/docs/reference/configuration-reference.md``;
* every MCP tool registered in ``src/graph_mcp/server.py`` is mentioned
  in ``docs-site/docs/reference/tools-reference.md`` and the user-guide
  overview ``docs-site/docs/users/mcp-tools.md``;
* every MCP resource URI registered in ``src/graph_mcp/server.py`` is
  mentioned in ``docs-site/docs/reference/resources-reference.md`` and
  the user-guide overview ``docs-site/docs/users/mcp-resources.md``;
* every MCP prompt registered in ``src/graph_mcp/server.py`` is
  mentioned in ``docs-site/docs/reference/prompts-reference.md``;
* every anchor referenced from a generated managed table actually
  exists as a heading in the same file;
* no accidental hardcoded GitHub placeholder URLs remain;
* no unauthorized placeholder strings (``TODO``, ``TBD``, ``FIXME``)
  outside the explicit "Known limitations" / "Roadmap" / "Future work"
  / "Open questions" sections;
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
PROMPTS_REF = DOCS_DIR / "reference" / "prompts-reference.md"
USER_TOOLS = DOCS_DIR / "users" / "mcp-tools.md"
USER_RESOURCES = DOCS_DIR / "users" / "mcp-resources.md"
BUILD_DIR = DOCS_SITE / "build"

# Placeholder strings that fail the check unless they appear in a
# "Known limitations" / "Roadmap" / "Future work" section.
PLACEHOLDER_RE = re.compile(r"\b(TODO|TBD|FIXME)\b")
ALLOWED_HEADERS = ("Known limitations", "Future work", "Roadmap", "Open questions")

# Hardcoded GitHub placeholders that should never appear in committed
# Markdown — they end up as live links to a likely-nonexistent repo.
# We allow the documented "YOUR_ORG_OR_USER" placeholder used as an
# instructional substitution.
FORBIDDEN_URL_PATTERNS = [
    "https://github.com/graph-mcp/graph-mcp",
    "https://github.com/<owner>",
    "<owner>/graph-mcp",
]


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
        return re.findall(
            r"@mcp\.tool\(\)\s+(?:async\s+)?def\s+([A-Za-z_][A-Za-z_0-9]*)",
            src,
        )
    if decorator == "resource":
        return re.findall(r'@mcp\.resource\("([^"]+)"\)', src)
    if decorator == "prompt":
        return re.findall(r'@mcp\.prompt\("([^"]+)"\)', src)
    raise ValueError(f"unsupported decorator: {decorator}")


def doc_files(prefix: Path) -> list[Path]:
    return sorted(p for p in prefix.rglob("*.md") if p.is_file())


def heading_anchors(text: str) -> set[str]:
    """Return the slug for every ATX heading (``# `` … ``###### ``).

    The slug rule mirrors Docusaurus' default GitHub-flavored slugger:
    lowercase, replace whitespace with `-`, strip surrounding non-word
    characters, and drop characters not in [a-z0-9-_].
    """
    out: set[str] = set()
    for line in text.splitlines():
        m = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if not m:
            continue
        title = m.group(1).strip()
        slug = title.lower()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"\s+", "-", slug)
        slug = slug.strip("-")
        if slug:
            out.add(slug)
    return out


def check_managed_anchors(problems: list[str], path: Path, table_name: str) -> None:
    """Verify each ``[Details](#anchor)`` link inside a managed table
    points to a heading that actually exists in the same document.
    """
    if not path.exists():
        return
    text = path.read_text()
    begin = re.escape(f"<!-- BEGIN: managed:{table_name} -->")
    end = re.escape(f"<!-- END: managed:{table_name} -->")
    pattern = re.compile(begin + r"(.*?)" + end, flags=re.DOTALL)
    m = pattern.search(text)
    if not m:
        return
    anchors_in_table = re.findall(r"\(#([A-Za-z0-9_\-]+)\)", m.group(1))
    headings = heading_anchors(text)
    for anchor in anchors_in_table:
        if anchor not in headings:
            fail(
                f"managed table {table_name!r} in {path.relative_to(ROOT)} "
                f"links to anchor {anchor!r} but no matching heading "
                "exists in the same document.",
                problems=problems,
            )


def check_placeholders(problems: list[str]) -> None:
    for path in doc_files(DOCS_DIR):
        text = path.read_text()
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


def check_forbidden_urls(problems: list[str]) -> None:
    """Flag hardcoded placeholder GitHub URLs anywhere in the docs."""
    for path in doc_files(DOCS_DIR):
        text = path.read_text()
        for pattern in FORBIDDEN_URL_PATTERNS:
            if pattern in text:
                fail(
                    f"forbidden placeholder URL {pattern!r} in "
                    f"{path.relative_to(ROOT)}; replace with a "
                    "repo-relative path or 'YOUR_ORG_OR_USER'.",
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
    tools = server_decorations("tool")
    if not tools:
        fail(
            "no @mcp.tool() decorators found in server.py — refusing to claim full tool coverage",
            problems=problems,
        )
        return
    ref_text = TOOLS_REF.read_text()
    user_text = USER_TOOLS.read_text() if USER_TOOLS.exists() else ""
    for t in tools:
        if t not in ref_text:
            fail(
                f"MCP tool {t!r} is registered in server.py but not "
                f"mentioned in {TOOLS_REF.relative_to(ROOT)}",
                problems=problems,
            )
        if user_text and t not in user_text:
            fail(
                f"MCP tool {t!r} is registered in server.py but not "
                f"mentioned in the user overview {USER_TOOLS.relative_to(ROOT)}",
                problems=problems,
            )


def check_resource_coverage(problems: list[str]) -> None:
    if not RESOURCES_REF.exists():
        fail(
            f"missing reference page {RESOURCES_REF.relative_to(ROOT)}",
            problems=problems,
        )
        return
    resources = server_decorations("resource")
    if not resources:
        fail(
            "no @mcp.resource(...) decorators found in server.py",
            problems=problems,
        )
        return
    ref_text = RESOURCES_REF.read_text()
    user_text = USER_RESOURCES.read_text() if USER_RESOURCES.exists() else ""
    for r in resources:
        if r not in ref_text:
            fail(
                f"MCP resource {r!r} is registered in server.py but not "
                f"mentioned in {RESOURCES_REF.relative_to(ROOT)}",
                problems=problems,
            )
        if user_text and r not in user_text:
            fail(
                f"MCP resource {r!r} is registered in server.py but not "
                f"mentioned in the user overview {USER_RESOURCES.relative_to(ROOT)}",
                problems=problems,
            )


def check_prompt_coverage(problems: list[str]) -> None:
    if not PROMPTS_REF.exists():
        fail(
            f"missing reference page {PROMPTS_REF.relative_to(ROOT)}",
            problems=problems,
        )
        return
    prompts = server_decorations("prompt")
    if not prompts:
        fail(
            "no @mcp.prompt(...) decorators found in server.py",
            problems=problems,
        )
        return
    text = PROMPTS_REF.read_text()
    for name in prompts:
        if name not in text:
            fail(
                f"MCP prompt {name!r} is registered in server.py but not "
                f"mentioned in {PROMPTS_REF.relative_to(ROOT)}",
                problems=problems,
            )


def check_anchor_consistency(problems: list[str]) -> None:
    """Verify managed tables only link to anchors that exist."""
    check_managed_anchors(problems, TOOLS_REF, "tools-table")
    check_managed_anchors(problems, RESOURCES_REF, "resources-table")
    check_managed_anchors(problems, PROMPTS_REF, "prompts-table")


def check_env_descriptions(problems: list[str]) -> None:
    """Every GRAPH_MCP_* variable must have a real description.

    The doc generator falls back to ``_(undocumented)_`` when
    ``.env.example`` does not have a comment above the assignment;
    that fallback should never end up in published docs.
    """
    if not CONFIG_REF.exists():
        return
    text = CONFIG_REF.read_text()
    if "_(undocumented)_" in text:
        fail(
            "configuration-reference.md contains '_(undocumented)_' — "
            "add a comment above the corresponding GRAPH_MCP_* line in "
            ".env.example and re-run scripts/generate_docs_reference.py.",
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
    check_env_descriptions(problems)
    check_tool_coverage(problems)
    check_resource_coverage(problems)
    check_prompt_coverage(problems)
    check_anchor_consistency(problems)
    check_forbidden_urls(problems)
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
