"""Token-aware SPARQL scanner used for raw-mode safety checks.

This is **not** a full SPARQL parser. It is a small lexical scanner whose
job is to:

- distinguish code from string literals, comments, and IRIs;
- emit a stream of tokens we can use to detect forbidden update keywords,
  detect ``DESCRIBE``, find ``SERVICE`` endpoints, and infer the query form
  from the first query keyword;
- correctly handle ``#`` as a comment marker only in *default* state — not
  inside ``"..."``/``'...'`` strings, triple-quoted strings, or IRI refs
  (``<...>``), so that ``<http://example.org/#fragment>`` is never mistaken
  for a comment.

The scanner is conservative: anything that looks like a string or IRI is
treated as opaque, and any keyword inside such a region is invisible to the
safety checks.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class TokenKind(StrEnum):
    KEYWORD = "keyword"
    """An identifier-shaped token in default state. The scanner does not know
    SPARQL grammar; safety checks treat keywords case-insensitively."""

    PUNCT = "punct"
    """Single non-alphabetic character we keep just so callers can find
    ``{``, ``}``, ``(``, ``)``, etc."""

    STRING = "string"
    """A SPARQL string literal (any quoting form)."""

    IRI = "iri"
    """An IRI reference. The IRI's interior (without the angle brackets) is
    available as ``Token.value``."""

    PREFIXED = "prefixed"
    """A ``prefix:local`` token in default state."""

    NUMBER = "number"

    COMMENT = "comment"


@dataclass(frozen=True)
class Token:
    kind: TokenKind
    value: str
    """The token text. For ``IRI`` this is the contents (no angle brackets);
    for ``STRING`` it is the lexeme including quotes."""

    start: int
    """Byte offset of the start of the token in the source."""


_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
_PREFIXED_RE = re.compile(r"([A-Za-z_][A-Za-z_0-9\-.]*):([A-Za-z_0-9][A-Za-z_0-9\-.~]*)")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?")
_VAR_RE = re.compile(r"[?$][A-Za-z_][A-Za-z_0-9]*")


def tokenize(sparql: str) -> list[Token]:
    """Return a list of :class:`Token` for ``sparql``.

    Comments and whitespace are dropped (we do not emit ``COMMENT`` tokens to
    callers). The scanner does not parse SPARQL syntax — it only segments
    the input into safe, opaque regions.
    """
    out: list[Token] = []
    i = 0
    n = len(sparql)
    while i < n:
        c = sparql[i]
        # Whitespace.
        if c.isspace():
            i += 1
            continue
        # Comment.
        if c == "#":
            while i < n and sparql[i] != "\n":
                i += 1
            continue
        # IRI reference.
        if c == "<":
            j = i + 1
            while j < n and sparql[j] != ">":
                # IRIs do not contain raw newlines or control whitespace.
                if sparql[j] == "\n":
                    raise _ScannerError("unterminated IRI reference", i)
                j += 1
            if j >= n:
                raise _ScannerError("unterminated IRI reference", i)
            out.append(Token(TokenKind.IRI, sparql[i + 1 : j], i))
            i = j + 1
            continue
        # Triple-quoted strings.
        if sparql.startswith('"""', i) or sparql.startswith("'''", i):
            quote = sparql[i : i + 3]
            j = i + 3
            while j < n and not sparql.startswith(quote, j):
                if sparql[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                j += 1
            if j >= n:
                raise _ScannerError("unterminated triple-quoted string", i)
            out.append(Token(TokenKind.STRING, sparql[i : j + 3], i))
            i = j + 3
            continue
        # Single-line strings.
        if c in ('"', "'"):
            quote = c
            j = i + 1
            while j < n and sparql[j] != quote:
                if sparql[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if sparql[j] == "\n":
                    raise _ScannerError("unterminated string literal", i)
                j += 1
            if j >= n:
                raise _ScannerError("unterminated string literal", i)
            out.append(Token(TokenKind.STRING, sparql[i : j + 1], i))
            i = j + 1
            continue
        # Variable.
        m = _VAR_RE.match(sparql, i)
        if m:
            out.append(Token(TokenKind.KEYWORD, m.group(0), i))
            i = m.end()
            continue
        # Prefixed name (must be tried before bare keyword so we eat the colon).
        m = _PREFIXED_RE.match(sparql, i)
        if m:
            out.append(Token(TokenKind.PREFIXED, m.group(0), i))
            i = m.end()
            continue
        # Identifier / keyword.
        m = _IDENT_RE.match(sparql, i)
        if m:
            out.append(Token(TokenKind.KEYWORD, m.group(0), i))
            i = m.end()
            continue
        # Number.
        m = _NUMBER_RE.match(sparql, i)
        if m:
            out.append(Token(TokenKind.NUMBER, m.group(0), i))
            i = m.end()
            continue
        # Otherwise: a single punctuation character. We keep it so callers
        # can detect blocks if they need to, but safety analysis ignores it.
        out.append(Token(TokenKind.PUNCT, c, i))
        i += 1
    return out


class _ScannerError(ValueError):
    def __init__(self, msg: str, pos: int) -> None:
        super().__init__(f"{msg} at position {pos}")
        self.pos = pos


# --- Safety analysis built on the token stream ---------------------------


_UPDATE_KEYWORDS: frozenset[str] = frozenset(
    {
        "INSERT",
        "DELETE",
        "DROP",
        "CLEAR",
        "LOAD",
        "CREATE",
        "COPY",
        "MOVE",
        "ADD",
        "WITH",
    }
)
_FORBIDDEN_QUERY_FORMS: frozenset[str] = frozenset({"DESCRIBE"})
_QUERY_FORM_KEYWORDS: tuple[str, ...] = ("SELECT", "ASK", "CONSTRUCT", "DESCRIBE")


def find_keyword(tokens: Iterable[Token], target: str) -> Token | None:
    """Return the first KEYWORD token whose text matches ``target`` (case-insensitive)."""
    upper = target.upper()
    for t in tokens:
        if t.kind is TokenKind.KEYWORD and t.value.upper() == upper:
            return t
    return None


def infer_query_type(tokens: list[Token]) -> str:
    """Return ``select``, ``ask``, or ``construct`` based on the first query keyword.

    ``DESCRIBE`` raises :class:`PermissionError`. Raises if no query keyword
    is present.
    """
    for t in tokens:
        if t.kind is not TokenKind.KEYWORD:
            continue
        upper = t.value.upper()
        if upper == "DESCRIBE":
            raise PermissionError("DESCRIBE is not supported in raw mode")
        if upper in _QUERY_FORM_KEYWORDS:
            return upper.lower()
    raise PermissionError("could not determine SPARQL query form")


def _next_non_punct(tokens: list[Token], start: int) -> tuple[int, Token | None]:
    """Return ``(index, token)`` of the first non-PUNCT token at or after ``start``.

    Returns ``(len(tokens), None)`` when none is found.
    """
    j = start
    while j < len(tokens):
        if tokens[j].kind is not TokenKind.PUNCT:
            return j, tokens[j]
        j += 1
    return len(tokens), None


def reject_unsafe_raw(
    sparql: str,
    *,
    allowed_service_endpoints: frozenset[str],
) -> list[Token]:
    """Pre-flight safety check on raw SPARQL using a real token scan.

    Raises :class:`PermissionError` for any forbidden form. Returns the
    token list on success so callers can avoid re-tokenizing.
    """
    try:
        tokens = tokenize(sparql)
    except _ScannerError as exc:
        raise PermissionError(f"could not tokenize raw SPARQL: {exc}") from exc

    # Forbidden update / query-form keywords.
    for tok in tokens:
        if tok.kind is not TokenKind.KEYWORD:
            continue
        upper = tok.value.upper()
        if upper in _UPDATE_KEYWORDS:
            raise PermissionError(f"forbidden SPARQL keyword in raw query: {upper}")
        if upper in _FORBIDDEN_QUERY_FORMS:
            raise PermissionError(f"unsupported query form: {upper}")

    # SERVICE handling: each occurrence must be followed by an absolute IRI
    # that exactly matches the allowlist. ``SERVICE SILENT`` is permitted.
    for idx, tok in enumerate(tokens):
        if tok.kind is not TokenKind.KEYWORD or tok.value.upper() != "SERVICE":
            continue
        # Skip an optional SILENT keyword.
        nxt_idx, nxt = _next_non_punct(tokens, idx + 1)
        if nxt is not None and nxt.kind is TokenKind.KEYWORD and nxt.value.upper() == "SILENT":
            nxt_idx, nxt = _next_non_punct(tokens, nxt_idx + 1)
        if nxt is None:
            raise PermissionError("SERVICE requires an endpoint")
        if nxt.kind is TokenKind.KEYWORD and nxt.value.startswith(("?", "$")):
            raise PermissionError("SERVICE with a variable endpoint is not permitted")
        if nxt.kind is TokenKind.PREFIXED:
            raise PermissionError(
                "SERVICE with a prefixed-name endpoint is not permitted in raw mode; "
                "use an absolute IRI"
            )
        if nxt.kind is not TokenKind.IRI:
            raise PermissionError(f"SERVICE must be followed by an absolute IRI, got {nxt.value!r}")
        if nxt.value not in allowed_service_endpoints:
            raise PermissionError(f"SERVICE endpoint not in allowlist: {nxt.value}")

    return tokens


_INTEGER_LIMIT_RE = re.compile(r"^[0-9]+$")


@dataclass(frozen=True)
class LimitScanResult:
    """Outcome of scanning a token stream for top-level ``LIMIT`` clauses.

    ``found`` is True when at least one top-level ``LIMIT`` was seen. ``count``
    is the total number of distinct top-level ``LIMIT`` keywords (multiple
    occurrences are an error).  ``value`` is the integer limit when exactly
    one valid limit was found.  ``error`` carries a human-readable message
    explaining why the result is unusable (e.g. negative number, decimal,
    ``+``-signed, multiple occurrences, missing operand).
    """

    found: bool
    value: int | None = None
    count: int = 0
    error: str | None = None


def find_top_level_limit(tokens: list[Token]) -> LimitScanResult:
    """Find the top-level ``LIMIT <n>`` and validate its form.

    "Top-level" here means: the ``LIMIT`` keyword sits at the same brace
    nesting depth as the leading query keyword (depth 0). The operand must
    be a non-negative decimal integer with no ``+`` sign, no decimal point,
    and no exponent. Multiple top-level ``LIMIT`` clauses are reported as an
    error rather than silently picking the last one.
    """
    depth = 0
    count = 0
    value: int | None = None
    error: str | None = None
    for idx, tok in enumerate(tokens):
        if tok.kind is TokenKind.PUNCT and tok.value == "{":
            depth += 1
            continue
        if tok.kind is TokenKind.PUNCT and tok.value == "}":
            depth -= 1
            continue
        if depth != 0:
            continue
        if tok.kind is not TokenKind.KEYWORD or tok.value.upper() != "LIMIT":
            continue
        count += 1
        # The immediate next token (not skipping any PUNCT) must be a NUMBER.
        # This rejects ``LIMIT + 1`` and ``LIMIT ( 1 )`` rather than silently
        # accepting them via PUNCT-skipping.
        nxt = tokens[idx + 1] if idx + 1 < len(tokens) else None
        if nxt is None:
            if error is None:
                error = "LIMIT requires an integer operand"
            continue
        if nxt.kind is not TokenKind.NUMBER:
            if error is None:
                error = f"LIMIT operand must be a non-negative integer; got token {nxt.value!r}"
            continue
        # Reject signs, decimals, exponents — only bare unsigned integers
        # are permitted as a top-level LIMIT.
        if not _INTEGER_LIMIT_RE.match(nxt.value):
            if error is None:
                error = f"LIMIT operand must be a non-negative integer literal; got {nxt.value!r}"
            continue
        try:
            this_value = int(nxt.value)
        except ValueError:  # pragma: no cover - regex guarantees parseability
            if error is None:
                error = f"LIMIT operand could not be parsed as an integer: {nxt.value!r}"
            continue
        if this_value < 0:  # pragma: no cover - regex guarantees non-negative
            if error is None:
                error = f"LIMIT must be >= 0; got {this_value}"
            continue
        value = this_value
    if count > 1 and error is None:
        error = f"multiple top-level LIMIT clauses are not allowed (found {count})"
        # Discard the value so the caller can't accept the last-one-wins behavior.
        value = None
    if error is not None:
        return LimitScanResult(found=count > 0, value=None, count=count, error=error)
    return LimitScanResult(found=count == 1, value=value, count=count)
