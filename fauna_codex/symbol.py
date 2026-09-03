"""Shared symbol record — used by scan.py's ast parser and every fauna_codex.parsers backend.
Its own module so parsers/ and scan.py can both depend on it without a circular import."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Symbol:
    section: str
    name: str
    file: str
    line: int
    signature: str
    docstring: str
    language: str
