"""Regression test — fauna_codex.semantic key collision on duplicate names, fixed.

`entry_key(section, row)` used to omit the line number, so two distinct
Symbol entries that share (section, name, file) but differ only by line —
e.g. two module-level functions with the same name in one file, which
`parse_python_file` legitimately emits as two separate 'python_functions'
rows — collided under the same key in `index['entries']`. `cmd_embed` then
let the second silently overwrite the first: one real symbol became
permanently invisible to semantic search, with no error or warning.

Fix: `entry_key` now includes `row['line']`, so same-named symbols in the
same file get distinct keys. This test proves the collision is gone.
"""
from __future__ import annotations

from conftest import write

from fauna_codex.scan import build_atlas
from fauna_codex.semantic import entry_key, iter_atlas_entries


def test_duplicate_function_name_collides_on_entry_key(tmp_path):
    # Two module-level functions named `process`, at clearly different lines.
    # Legal Python: ast still visits and records both, even though the second
    # shadows the first at runtime.
    write(tmp_path, "mod.py", '''
def process(a):
    """First process."""
    return a


def process(b):
    """Second process, shadows the first."""
    return b * 2
''')

    atlas = build_atlas(tmp_path)
    entries = iter_atlas_entries(atlas)

    process_entries = [e for e in entries if e["name"] == "process" and e["file"] == "mod.py"]

    # Both symbols were extracted, at different lines.
    assert len(process_entries) == 2
    assert {e["line"] for e in process_entries} == {2, 7}

    # ...and now they get distinct index keys — no more silent overwrite.
    keys = {entry_key(e["section"], e) for e in process_entries}
    assert len(keys) == 2
