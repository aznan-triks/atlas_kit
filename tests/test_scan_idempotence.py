"""Tests — a rescan of an unchanged tree must produce a BYTE-IDENTICAL atlas.json.

Agent loops diff successive atlases; any non-determinism (set iteration order, unsorted
edges, a reuse path that reorders rows) would show up as phantom churn. Comparing raw
file bytes is the only assertion that catches all of those at once.
"""
from __future__ import annotations

import pytest
from conftest import write

from fauna_codex.index_store import save_json
from fauna_codex.scan import build_atlas

TREE = {
    "pkg/__init__.py": "",
    "pkg/core.py": '''
import os
from .helpers import shout, whisper


class Engine:
    def run(self, name):
        shout(name)
        shout(name)
        return os.getcwd()

    def stop(self):
        whisper()


Engine()
''',
    "pkg/helpers.py": "def shout(name):\n    return name.upper()\n\n\ndef whisper():\n    return 1\n",
    "app.js": "function boot() { start(); }\n",
}


@pytest.fixture
def tree(tmp_path):
    for rel, content in TREE.items():
        write(tmp_path, rel, content)
    return tmp_path


def _dump(atlas: dict, path) -> bytes:
    save_json(path, atlas)
    return path.read_bytes()


def test_two_cold_scans_are_byte_identical(tree, tmp_path):
    first = _dump(build_atlas(tree), tmp_path / "out" / "first.json")
    second = _dump(build_atlas(tree), tmp_path / "out" / "second.json")
    assert first == second


def test_incremental_rescan_is_byte_identical_to_cold_scan(tree, tmp_path):
    cold = build_atlas(tree)
    first = _dump(cold, tmp_path / "out" / "cold.json")
    # Every file hash is unchanged, so this run takes the reuse path end to end.
    second = _dump(build_atlas(tree, previous=cold), tmp_path / "out" / "incremental.json")
    assert first == second


def test_incremental_rescan_after_edit_matches_a_cold_scan(tree, tmp_path):
    cold = build_atlas(tree)
    write(tree, "pkg/helpers.py",
          "def shout(name):\n    return name.upper()\n\n\ndef murmur():\n    return 2\n")

    incremental = _dump(build_atlas(tree, previous=cold), tmp_path / "out" / "incremental.json")
    fresh = _dump(build_atlas(tree), tmp_path / "out" / "fresh.json")
    assert incremental == fresh


def test_edges_survive_the_reuse_path_unchanged(tree):
    cold = build_atlas(tree)
    reused = build_atlas(tree, previous=cold)
    assert reused["edges"] == cold["edges"]
    assert cold["edges"]["calls"], "fixture must produce at least one call edge"
