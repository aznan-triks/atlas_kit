"""Tests — atlas_kit.semantic.cmd_embed: key-schema migration and stale-entry pruning.

Both scenarios are constructed so pending_entries() returns nothing to embed
(the stored hash already matches), so cmd_embed never needs to make a real
provider call — only the local, deterministic rekey/prune/centroid logic runs.
"""
from __future__ import annotations

import json

from conftest import write

from atlas_kit.index_store import load_json
from atlas_kit.semantic import CURRENT_KEY_SCHEMA, cmd_embed, entry_key, iter_atlas_entries
from atlas_kit.scan import build_atlas

MODEL = "test-model"
DIM = 4


def _atlas_with_one_entry(tmp_path):
    write(tmp_path, "mod.py", 'def foo(a):\n    """Do foo."""\n    return a\n')
    atlas_path = tmp_path / "atlas.json"
    atlas = build_atlas(tmp_path)
    atlas_path.write_text(json.dumps(atlas), encoding="utf-8")
    return atlas_path, atlas


def test_embed_migrates_old_format_keys_and_preserves_vectors(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    atlas_path, atlas = _atlas_with_one_entry(tmp_path)
    entries = iter_atlas_entries(atlas, model=MODEL, dim=DIM)
    assert len(entries) == 1
    entry = entries[0]

    old_key = f"{entry['section']}::{entry['name']}::{entry['file']}"  # pre-fix format, no line
    vector = [0.1, 0.2, 0.3, 0.4]
    index_path = tmp_path / "semantic_index.json"
    index_path.write_text(json.dumps({
        "model": MODEL, "dim": DIM,
        "entries": {old_key: {
            "section": entry["section"], "name": entry["name"], "file": entry["file"],
            "line": entry["line"], "signature": entry["signature"], "docstring": entry["docstring"],
            "hash": entry["hash"], "vector": vector,
        }},
        # no key_schema field — simulates a pre-migration index.
    }), encoding="utf-8")

    code = cmd_embed(atlas_path, index_path, "gemini", MODEL, DIM, 50, 5.0)
    out = capsys.readouterr().out
    assert code == 0
    assert "Migrated 1 index key" in out

    index = load_json(index_path, {})
    assert index.get("key_schema") == CURRENT_KEY_SCHEMA
    new_key = entry_key(entry["section"], entry)
    assert new_key != old_key
    assert old_key not in index["entries"]
    assert index["entries"][new_key]["vector"] == vector


def test_embed_prunes_orphaned_entries_with_no_matching_atlas_entry(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    atlas_path, atlas = _atlas_with_one_entry(tmp_path)
    entries = iter_atlas_entries(atlas, model=MODEL, dim=DIM)
    entry = entries[0]
    current_key = entry_key(entry["section"], entry)
    current_vector = [1.0, 0.0, 0.0, 0.0]

    orphan_key = "python_functions::ghost::mod.py::999"
    orphan_value = {
        "section": "python_functions", "name": "ghost", "file": "mod.py", "line": 999,
        "signature": "", "docstring": "", "hash": "irrelevant", "vector": [0.0, 1.0, 0.0, 0.0],
    }

    index_path = tmp_path / "semantic_index.json"
    index_path.write_text(json.dumps({
        "model": MODEL, "dim": DIM, "key_schema": CURRENT_KEY_SCHEMA,
        "entries": {
            current_key: {
                "section": entry["section"], "name": entry["name"], "file": entry["file"],
                "line": entry["line"], "signature": entry["signature"], "docstring": entry["docstring"],
                "hash": entry["hash"], "vector": current_vector,
            },
            orphan_key: orphan_value,
        },
    }), encoding="utf-8")

    code = cmd_embed(atlas_path, index_path, "gemini", MODEL, DIM, 50, 5.0)
    out = capsys.readouterr().out
    assert code == 0
    assert "Pruned 1 stale index entrie(s)" in out

    index = load_json(index_path, {})
    assert orphan_key not in index["entries"]
    assert current_key in index["entries"]
    # centroid recomputed over the sole remaining (current) vector.
    assert index["centroid"] == current_vector
