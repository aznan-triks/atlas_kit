"""`diff` — offline comparison of two atlas snapshots. No network, no API key.

Answers "what changed in this repo's symbols between two scans?" without re-reading
the whole atlas: which files appeared, vanished or changed content, and which symbols
were added, removed, moved or re-signed.

`diff` is a REPORT, not a gate: once the comparison actually ran it exits 0, whether
or not it found differences. Only an unusable input (a missing file, an incompatible
atlas schema) is a failure — never a silently empty comparison, which would report a
whole repository as deleted. A caller that wants a gate reads the integer counts from
the `--json` payload, e.g. `summary.symbols_removed > 0`.

Unknown top-level atlas keys (`edges`, or anything added later) are ignored on
purpose: this module only reads `files` and `symbols`.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from fauna_codex import emit
from fauna_codex.index_store import atlas_schema_error, load_json

COMMAND: Final = "diff"

EXIT_OK: Final = 0
# Unusable input — the user must fix a path or re-run `scan`. Mirrors cli.EXIT_NEEDS_USER.
EXIT_NEEDS_USER: Final = 2

# Human output only. The JSON payload is never truncated, so a machine consumer keeps
# the full lists while a human keeps a readable screen.
MAX_HUMAN_LINES_PER_GROUP: Final = 20


@dataclass(frozen=True)
class AtlasDiff:
    """The complete result of comparing two atlases. Pure data — no I/O, no printing.

    A symbol that both moved and changed signature appears in BOTH `symbols_moved` and
    `symbols_signature_changed`: they are independent facts about the same symbol, and
    a consumer gating on signature changes must not miss one because it also moved.
    """

    files_added: list[str] = field(default_factory=list)
    files_removed: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    symbols_added: list[dict] = field(default_factory=list)
    symbols_removed: list[dict] = field(default_factory=list)
    symbols_moved: list[dict] = field(default_factory=list)
    symbols_signature_changed: list[dict] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        """Integer counts for every category — the stable surface a CI script gates on."""
        return {
            "files_added": len(self.files_added),
            "files_removed": len(self.files_removed),
            "files_changed": len(self.files_changed),
            "symbols_added": len(self.symbols_added),
            "symbols_removed": len(self.symbols_removed),
            "symbols_moved": len(self.symbols_moved),
            "symbols_signature_changed": len(self.symbols_signature_changed),
        }

    @property
    def total_changes(self) -> int:
        return sum(self.summary().values())


def symbol_identity(section: str, row: dict) -> str:
    """`section::name::file` — deliberately line-independent, so a symbol that only
    shifted down the file is recognised as the same symbol (a move, not add+remove)."""
    return f"{section}::{row.get('name', '')}::{row.get('file', '')}"


def _symbol_map(atlas: dict) -> dict[str, dict]:
    """Flatten `atlas["symbols"]` into one {lookup key -> normalised row} map.

    One file can legitimately hold two symbols sharing an identity (a name bound twice,
    an overload). They are kept apart by an occurrence suffix assigned in line order, so
    neither is silently dropped by the other — the reported `key` stays the plain
    identity. Same input order in both atlases therefore pairs the same occurrences.
    """
    out: dict[str, dict] = {}
    occurrences: Counter[str] = Counter()
    for section, rows in (atlas.get("symbols") or {}).items():
        for row in sorted(rows, key=lambda r: int(r.get("line") or 0)):
            identity = symbol_identity(section, row)
            occurrences[identity] += 1
            nth = occurrences[identity]
            lookup = identity if nth == 1 else f"{identity}#{nth}"
            out[lookup] = {
                "key": identity,
                "section": section,
                "name": row.get("name", ""),
                "file": row.get("file", ""),
                "line": int(row.get("line") or 0),
                "signature": row.get("signature", "") or "",
            }
    return out


def _sorted_rows(rows: list[dict]) -> list[dict]:
    """Deterministic output order — a diff piped into a review must not reshuffle."""
    return sorted(rows, key=lambda r: (r["section"], r["file"], r["name"], r.get("line", 0)))


def diff_atlases(old: dict, new: dict) -> AtlasDiff:
    """Compare two loaded atlas dicts. Pure: no file access, no printing, no exit code.

    Callers pass already-validated atlases — schema validation belongs to `cmd_diff`,
    so this function stays trivially testable without touching the filesystem.
    """
    old_files: dict[str, str] = old.get("files") or {}
    new_files: dict[str, str] = new.get("files") or {}
    # Only relpaths are reported: the sha256 itself tells a caller nothing it can act on.
    files_added = sorted(set(new_files) - set(old_files))
    files_removed = sorted(set(old_files) - set(new_files))
    files_changed = sorted(
        rel for rel in set(old_files) & set(new_files) if old_files[rel] != new_files[rel]
    )

    old_symbols = _symbol_map(old)
    new_symbols = _symbol_map(new)

    added = [new_symbols[k] for k in new_symbols.keys() - old_symbols.keys()]
    removed = [old_symbols[k] for k in old_symbols.keys() - new_symbols.keys()]

    moved: list[dict] = []
    signature_changed: list[dict] = []
    for key in old_symbols.keys() & new_symbols.keys():
        before, after = old_symbols[key], new_symbols[key]
        common = {"key": before["key"], "section": before["section"],
                  "name": before["name"], "file": before["file"]}
        if before["line"] != after["line"]:
            moved.append({**common, "line": after["line"],
                          "old_line": before["line"], "new_line": after["line"]})
        if before["signature"] != after["signature"]:
            signature_changed.append({**common, "line": after["line"],
                                      "old_signature": before["signature"],
                                      "new_signature": after["signature"]})

    return AtlasDiff(
        files_added=files_added,
        files_removed=files_removed,
        files_changed=files_changed,
        symbols_added=_sorted_rows(added),
        symbols_removed=_sorted_rows(removed),
        symbols_moved=_sorted_rows(moved),
        symbols_signature_changed=_sorted_rows(signature_changed),
    )


def _print_group(title: str, lines: list[str]) -> None:
    if not lines:
        return
    print(f"\n-- {title} ({len(lines)})")
    for line in lines[:MAX_HUMAN_LINES_PER_GROUP]:
        print(f"  {line}")
    hidden = len(lines) - MAX_HUMAN_LINES_PER_GROUP
    if hidden > 0:
        print(f"  ... {hidden} more (human output capped at {MAX_HUMAN_LINES_PER_GROUP} "
              f"per group — use --json for the complete list)")


def _print_human(result: AtlasDiff, old_path: Path, new_path: Path) -> None:
    counts = result.summary()
    print(f"diff {old_path} -> {new_path}")
    print(f"files   : +{counts['files_added']} -{counts['files_removed']} "
          f"~{counts['files_changed']} changed")
    print(f"symbols : +{counts['symbols_added']} -{counts['symbols_removed']} "
          f"{counts['symbols_moved']} moved {counts['symbols_signature_changed']} re-signed")
    if result.total_changes == 0:
        print("\nNo differences.")
        return

    _print_group("files added", result.files_added)
    _print_group("files removed", result.files_removed)
    _print_group("files changed", result.files_changed)
    _print_group("symbols added", [
        f"{r['section']}  {r['name']}  {r['file']}:{r['line']}" for r in result.symbols_added])
    _print_group("symbols removed", [
        f"{r['section']}  {r['name']}  {r['file']}:{r['line']}" for r in result.symbols_removed])
    _print_group("symbols moved", [
        f"{r['section']}  {r['name']}  {r['file']}:{r['old_line']} -> :{r['new_line']}"
        for r in result.symbols_moved])
    _print_group("symbols signature changed", [
        f"{r['section']}  {r['name']}  {r['file']}:{r['line']}  "
        f"{r['old_signature']} -> {r['new_signature']}"
        for r in result.symbols_signature_changed])


def cmd_diff(old_path: Path, new_path: Path, as_json: bool = False) -> int:
    """Compare two atlas files and report what changed. Returns the process exit code.

    Exits 0 as soon as the comparison ran — differences found or not. `diff` reports,
    it does not gate; gate on `summary` in the `--json` payload instead. Exits 2 when
    an input cannot be trusted (missing path, incompatible schema), because reporting
    an empty or wholesale-removed diff from a bad input is worse than failing loud.
    """
    atlases: list[dict] = []
    for label, path in (("old", old_path), ("new", new_path)):
        if not path.exists():
            emit.fail(COMMAND, f"Atlas not found ({label}): {path} — run `fauna-codex scan "
                               f"--out {path}` first, or pass an existing path. Refusing to "
                               f"treat a missing atlas as empty (that would report the whole "
                               f"repository as added or removed).", as_json)
            return EXIT_NEEDS_USER

        atlas = load_json(path, {})
        # Fail Fast: validate before reading anything out of it, never after.
        error = atlas_schema_error(atlas, path)
        if error:
            emit.fail(COMMAND, error, as_json)
            return EXIT_NEEDS_USER
        atlases.append(atlas)

    result = diff_atlases(atlases[0], atlases[1])

    if as_json:
        emit.json_ok(
            COMMAND,
            old=str(old_path),
            new=str(new_path),
            summary=result.summary(),
            files={"added": result.files_added, "removed": result.files_removed,
                   "changed": result.files_changed},
            symbols={"added": result.symbols_added, "removed": result.symbols_removed,
                     "moved": result.symbols_moved,
                     "signature_changed": result.symbols_signature_changed},
        )
    else:
        _print_human(result, old_path, new_path)
    return EXIT_OK
