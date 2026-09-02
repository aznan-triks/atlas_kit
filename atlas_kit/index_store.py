"""JSON read/write helpers shared by the mechanical atlas and the semantic index.

Also owns `ATLAS_SCHEMA_VERSION` — the format contract of `atlas.json`. Every reader
(`find`, `section`, `deps`, `embed`, `status`, `diff`) checks it before trusting the
file, so a future format change fails loud instead of being silently misread.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Final

# Bumped only when the on-disk atlas layout changes in a way an older/newer reader
# would misread. Atlases written before this field existed report version 0.
ATLAS_SCHEMA_VERSION: Final = 1


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


def atlas_schema_error(atlas: dict, path: Path) -> str | None:
    """Return an actionable message if `atlas` was written by an incompatible version,
    else None. Fail Fast: a reader calls this before using the atlas, never after.

    A missing `schema_version` (version 0) means an atlas written before the field
    existed — readable in principle, but it predates every field added since, so it
    is refused with a `scan` instruction rather than half-trusted.
    """
    found = int(atlas.get("schema_version") or 0)
    if found == ATLAS_SCHEMA_VERSION:
        return None
    if found > ATLAS_SCHEMA_VERSION:
        return (f"Atlas {path} was written by a newer atlas-kit (schema {found} > "
                f"{ATLAS_SCHEMA_VERSION}) — upgrade atlas-kit, or re-run `atlas-kit scan`.")
    return (f"Atlas {path} uses schema {found}, this atlas-kit expects "
            f"{ATLAS_SCHEMA_VERSION} — re-run `atlas-kit scan` to rebuild it.")
