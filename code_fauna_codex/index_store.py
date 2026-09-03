"""JSON read/write helpers shared by the mechanical codex and the semantic index.

Also owns `CODEX_SCHEMA_VERSION` — the format contract of `codex.json`. Every reader
(`find`, `section`, `deps`, `embed`, `status`, `diff`) checks it before trusting the
file, so a future format change fails loud instead of being silently misread.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Final

# Bumped only when the on-disk codex layout changes in a way an older/newer reader
# would misread. Codexes written before this field existed report version 0.
CODEX_SCHEMA_VERSION: Final = 1


def load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def codex_schema_error(codex: dict, path: Path) -> str | None:
    """Return an actionable message if `codex` was written by an incompatible version,
    else None. Fail Fast: a reader calls this before using the codex, never after.

    A missing `schema_version` (version 0) means a codex written before the field
    existed — readable in principle, but it predates every field added since, so it
    is refused with a `scan` instruction rather than half-trusted.
    """
    found = int(codex.get("schema_version") or 0)
    if found == CODEX_SCHEMA_VERSION:
        return None
    if found > CODEX_SCHEMA_VERSION:
        return (f"Codex {path} was written by a newer code-fauna-codex (schema {found} > "
                f"{CODEX_SCHEMA_VERSION}) — upgrade code-fauna-codex, or re-run `code-fauna-codex scan`.")
    return (f"Codex {path} uses schema {found}, this code-fauna-codex expects "
            f"{CODEX_SCHEMA_VERSION} — re-run `code-fauna-codex scan` to rebuild it.")
