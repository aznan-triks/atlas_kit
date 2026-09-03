"""Tests — the `.faunacodexignore` file at the scan root (union with `--ignore`, never a replacement)."""
from __future__ import annotations

from conftest import write

from fauna_codex.scan import build_atlas, read_ignore_file


def _names(atlas: dict) -> set[str]:
    return {row["name"] for rows in atlas["symbols"].values() for row in rows}


def test_read_ignore_file_absent_is_a_noop(tmp_path):
    assert read_ignore_file(tmp_path) == []


def test_read_ignore_file_skips_comments_and_blank_lines(tmp_path):
    write(tmp_path, ".faunacodexignore", "# a comment\n\n  generated/*  \n\n#another\nvendored/*\n")
    assert read_ignore_file(tmp_path) == ["generated/*", "vendored/*"]


def test_build_atlas_excludes_files_matched_by_faunacodexignore(tmp_path):
    write(tmp_path, "keep.py", "def kept():\n    pass\n")
    write(tmp_path, "generated/mod.py", "def generated_symbol():\n    pass\n")
    write(tmp_path, ".faunacodexignore", "generated/*\n")

    atlas = build_atlas(tmp_path)

    assert "kept" in _names(atlas)
    assert "generated_symbol" not in _names(atlas)
    assert set(atlas["files"]) == {"keep.py"}


def test_faunacodexignore_is_a_union_with_the_ignore_argument(tmp_path):
    write(tmp_path, "keep.py", "def kept():\n    pass\n")
    write(tmp_path, "generated/mod.py", "def from_file():\n    pass\n")
    write(tmp_path, "cli_skipped/mod.py", "def from_argument():\n    pass\n")
    write(tmp_path, ".faunacodexignore", "generated/*\n")

    atlas = build_atlas(tmp_path, ignore_globs=["cli_skipped/*"])

    # Neither source wins over the other — both exclusions apply.
    assert _names(atlas) == {"kept"}


def test_faunacodexignore_also_excludes_edges_for_the_ignored_file(tmp_path):
    write(tmp_path, "keep.py", "def kept():\n    import os\n    os.getcwd()\n")
    write(tmp_path, "generated/mod.py", "import json\n\n\ndef gen():\n    json.dumps({})\n")
    write(tmp_path, ".faunacodexignore", "generated/*\n")

    edges = build_atlas(tmp_path)["edges"]

    assert set(edges["imports"]) == {"keep.py"}
    assert {row["file"] for row in edges["calls"]} == {"keep.py"}
