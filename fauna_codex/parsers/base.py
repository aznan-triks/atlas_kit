"""Shared contract every parser backend implements. Zero backend-specific code here."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from fauna_codex.symbol import Symbol


class CodeParser(Protocol):
    name: str
    extensions: set[str]
    available: bool  # False if this backend can't handle a single one of its extensions

    def parse(self, path: Path, rel: str) -> list[Symbol]: ...


@runtime_checkable
class PerExtensionParser(Protocol):
    """Opt-in add-on for a backend whose availability varies per extension.

    tree-sitter ships one pip package per grammar, so `available` (a single bool) can't
    answer "can you do .go?". Kept OUT of CodeParser on purpose: a backend with a uniform
    runtime dependency (regex) stays a valid CodeParser without implementing anything.
    """

    def supports(self, extension: str) -> bool: ...


def supports_extension(parser: CodeParser, extension: str) -> bool:
    """Can `parser` actually handle `extension` right now?

    The one place the two availability shapes are reconciled, so the registry never has
    to know which backend is which.
    """
    if isinstance(parser, PerExtensionParser):
        return parser.supports(extension)
    return parser.available and extension in parser.extensions
