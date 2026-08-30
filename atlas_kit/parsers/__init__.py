"""Parser registry — extension -> backend, mirrors atlas_kit.providers.PROVIDERS.

'regex' is always available (every extension the old best-effort scanner covered).
'treesitter' covers only .js/.jsx/.ts/.tsx, and only when the optional 'treesitter'
extra is installed. Python is not part of this registry — it has exactly one engine
(ast, in scan.py) and no alternative backend is planned.
"""
from __future__ import annotations

from atlas_kit.parsers.base import CodeParser
from atlas_kit.parsers.regex_parser import RegexParser
from atlas_kit.parsers.treesitter_parser import TREESITTER_AVAILABLE, TreeSitterParser

PARSERS: dict[str, CodeParser] = {
    "regex": RegexParser(),
    "treesitter": TreeSitterParser(),
}

PARSER_MODES = ("auto", "regex", "treesitter")


def resolve_parser(extension: str, mode: str = "auto") -> CodeParser | None:
    """Pick a CodeParser for `extension` given `mode`.

    - 'regex': always the regex backend (pre-Plan-3 behaviour, unchanged).
    - 'treesitter': the tree-sitter backend for extensions it supports (.js/.jsx/.ts/.tsx).
      Raises RuntimeError if the optional dependency isn't installed — Fail Fast, never a
      silent fallback to regex when the user explicitly asked for tree-sitter. Extensions
      tree-sitter doesn't cover (.go/.rs) fall back to regex: the flag has no scope over
      them, there is no second backend for those languages yet.
    - 'auto' (default): tree-sitter for extensions it supports when installed, else regex.
    Returns None if no backend at all covers `extension` (unsupported file type).
    """
    regex = PARSERS["regex"]
    ts = PARSERS["treesitter"]

    if mode == "regex":
        return regex if extension in regex.extensions else None

    if mode == "treesitter":
        if extension not in ts.extensions:
            return regex if extension in regex.extensions else None
        if not ts.available:
            raise RuntimeError(
                "--parser treesitter requested but tree-sitter isn't installed — "
                "pip install 'atlas-kit[treesitter]'."
            )
        return ts

    if extension in ts.extensions and ts.available:
        return ts
    return regex if extension in regex.extensions else None
