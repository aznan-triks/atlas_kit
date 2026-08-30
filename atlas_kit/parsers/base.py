"""Shared contract every parser backend implements. Zero backend-specific code here."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from atlas_kit.symbol import Symbol


class CodeParser(Protocol):
    name: str
    extensions: set[str]
    available: bool  # False if this backend's optional runtime dependency isn't installed

    def parse(self, path: Path, rel: str) -> list[Symbol]: ...
