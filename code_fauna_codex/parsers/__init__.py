"""Parser registry — extension -> backend, mirrors code_fauna_codex.providers.PROVIDERS.

'regex' is always available (every extension the old best-effort scanner covered).
'treesitter' covers .js/.jsx/.ts/.tsx, .go and .rs, each behind its own optional grammar
package, so availability is decided PER EXTENSION (see base.supports_extension) rather
than per backend. Python is not part of this registry — it has exactly one engine
(ast, in scan.py) and no alternative backend is planned.
"""
from __future__ import annotations

from code_fauna_codex.parsers.base import CodeParser, supports_extension
from code_fauna_codex.parsers.regex_parser import RegexParser
from code_fauna_codex.parsers.treesitter_parser import (
    TREESITTER_AVAILABLE,
    TreeSitterParser,
    missing_grammar_message,
)

PARSERS: dict[str, CodeParser] = {
    "regex": RegexParser(),
    "treesitter": TreeSitterParser(),
}

PARSER_MODES = ("auto", "regex", "treesitter")


def resolve_parser(extension: str, mode: str = "auto") -> CodeParser | None:
    """Pick a CodeParser for `extension` given `mode`.

    - 'regex': always the regex backend (pre-Plan-3 behaviour, unchanged).
    - 'treesitter': the tree-sitter backend for every extension it covers
      (.js/.jsx/.ts/.tsx/.go/.rs). Raises RuntimeError naming the missing grammar package
      if that one grammar isn't installed — Fail Fast, never a silent fallback to regex
      when the user explicitly asked for tree-sitter. Extensions tree-sitter doesn't know
      at all fall back to regex: the flag has no scope over them.
    - 'auto' (default): tree-sitter for extensions whose grammar is importable, else regex.
    Returns None if no backend at all covers `extension` (unsupported file type).
    """
    regex = PARSERS["regex"]
    ts = PARSERS["treesitter"]

    if mode == "regex":
        return regex if extension in regex.extensions else None

    if mode == "treesitter":
        if extension not in ts.extensions:
            return regex if extension in regex.extensions else None
        if not supports_extension(ts, extension):
            raise RuntimeError(missing_grammar_message(extension))
        return ts

    if supports_extension(ts, extension):
        return ts
    return regex if extension in regex.extensions else None
