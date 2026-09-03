"""Tests — code_fauna_codex.semantic.cmd_embed: key-schema migration and stale-entry pruning.

Both scenarios are constructed so pending_entries() returns nothing to embed
(the stored hash already matches), so cmd_embed never needs to make a real
provider call — only the local, deterministic rekey/prune/centroid logic runs.
"""
from __future__ import annotations

import json

from conftest import write

from code_fauna_codex.index_store import load_json
from code_fauna_codex.semantic import CURRENT_KEY_SCHEMA, cmd_embed, entry_key, iter_codex_entries
from code_fauna_codex.scan import build_codex

MODEL = "test-model"
DIM = 4


def _codex_with_one_entry(tmp_path):
    write(tmp_path, "mod.py", 'def foo(a):\n    """Do foo."""\n    return a\n')
    codex_path = tmp_path / "codex.json"
    codex = build_codex(tmp_path)
    codex_path.write_text(json.dumps(codex), encoding="utf-8")
    return codex_path, codex


def test_embed_migrates_old_format_keys_and_preserves_vectors(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    codex_path, codex = _codex_with_one_entry(tmp_path)
    entries = iter_codex_entries(codex, model=MODEL, dim=DIM)
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

    code = cmd_embed(codex_path, index_path, "gemini", MODEL, DIM, 50, 5.0)
    out = capsys.readouterr().out
    assert code == 0
    assert "Migrated 1 index key" in out

    index = load_json(index_path, {})
    assert index.get("key_schema") == CURRENT_KEY_SCHEMA
    new_key = entry_key(entry["section"], entry)
    assert new_key != old_key
    assert old_key not in index["entries"]
    assert index["entries"][new_key]["vector"] == vector


def test_embed_prunes_orphaned_entries_with_no_matching_codex_entry(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    codex_path, codex = _codex_with_one_entry(tmp_path)
    entries = iter_codex_entries(codex, model=MODEL, dim=DIM)
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

    code = cmd_embed(codex_path, index_path, "gemini", MODEL, DIM, 50, 5.0)
    out = capsys.readouterr().out
    assert code == 0
    assert "Pruned 1 stale index entrie(s)" in out

    index = load_json(index_path, {})
    assert orphan_key not in index["entries"]
    assert current_key in index["entries"]
    # centroid recomputed over the sole remaining (current) vector.
    assert index["centroid"] == current_vector


def test_embed_warns_before_wiping_index_on_model_dim_mismatch(tmp_path, monkeypatch, capsys):
    """A stored index built with a different model/dim can never be migrated (different
    vector dimensions can't coexist) — but the reset must be announced, never silent."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    codex_path, codex = _codex_with_one_entry(tmp_path)

    def fake_post(url, headers, json_body, timeout):
        n = len(json_body["requests"])
        return _FakeResp(200, {"embeddings": [{"values": [1.0, 0.0, 0.0, 0.0]}] * n})

    import code_fauna_codex.providers.gemini as gemini_mod
    monkeypatch.setattr(gemini_mod, "_default_http_post", fake_post)

    index_path = tmp_path / "semantic_index.json"
    old_entries = {
        "old_section::old_name::old_file.py::1": {
            "section": "old_section", "name": "old_name", "file": "old_file.py", "line": 1,
            "signature": "", "docstring": "", "hash": "irrelevant", "vector": [0.0, 0.0, 0.0],
        },
    }
    index_path.write_text(json.dumps({
        "model": "some-old-model", "dim": 3, "key_schema": CURRENT_KEY_SCHEMA,
        "entries": old_entries,
    }), encoding="utf-8")

    code = cmd_embed(codex_path, index_path, "gemini", MODEL, DIM, 50, 5.0)
    err = capsys.readouterr().err
    assert code == 0
    assert "1 old vector(s) discarded" in err
    assert "some-old-model" in err and "'test-model'" in err

    index = load_json(index_path, {})
    assert "old_section::old_name::old_file.py::1" not in index["entries"]
    entry = iter_codex_entries(codex, model=MODEL, dim=DIM)[0]
    assert entry_key(entry["section"], entry) in index["entries"]


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload
