"""Regex-based fallback parser — best-effort line matching, not a real per-language
parser. Moved here unchanged from scan.py (Plan 3): owns its own patterns, like every
other backend in this registry, instead of scan.py hard-wiring the dispatch.
"""
from __future__ import annotations

import re
from pathlib import Path

from fauna_codex.symbol import Symbol

# One (regex, section) pair per extension group. Best-effort: catches the common
# declaration shapes, not every valid syntax variant of each language.
_GENERIC_PATTERNS: dict[str, list[tuple[re.Pattern, str]]] = {
    ".js": [
        (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s+([A-Za-z_$][\w$]*)"), "generic_functions"),
        (re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)"), "generic_classes"),
    ],
    ".go": [
        (re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\("), "generic_functions"),
    ],
    ".rs": [
        (re.compile(r"^\s*(?:pub\s+)?fn\s+([A-Za-z_]\w*)\s*[(<]"), "generic_functions"),
        (re.compile(r"^\s*(?:pub\s+)?struct\s+([A-Za-z_]\w*)"), "generic_classes"),
    ],
}
_GENERIC_PATTERNS[".jsx"] = _GENERIC_PATTERNS[".js"]
_GENERIC_PATTERNS[".ts"] = _GENERIC_PATTERNS[".js"]
_GENERIC_PATTERNS[".tsx"] = _GENERIC_PATTERNS[".js"]

_LANGUAGE_BY_EXT = {
    ".js": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".rs": "rust",
}


def parse_generic_file(path: Path, rel: str) -> list[Symbol]:
    patterns = _GENERIC_PATTERNS.get(path.suffix)
    if not patterns:
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return []

    out: list[Symbol] = []
    for lineno, line in enumerate(lines, start=1):
        for pattern, section in patterns:
            match = pattern.match(line)
            if match:
                out.append(Symbol(
                    section=section, name=match.group(1), file=rel, line=lineno,
                    signature=line.strip(), docstring="",
                    language=_LANGUAGE_BY_EXT[path.suffix],
                ))
    return out


class RegexParser:
    name = "regex"
    available = True
    extensions = set(_GENERIC_PATTERNS)

    def parse(self, path: Path, rel: str) -> list[Symbol]:
        return parse_generic_file(path, rel)
