"""Tests — the `.codefaunacodexignore` file at the scan root (union with `--ignore`, never a replacement)."""
from __future__ import annotations

from conftest import write

from code_fauna_codex.scan import build_codex, read_ignore_file


def _names(codex: dict) -> set[str]:
    return {row["name"] for rows in codex["symbols"].values() for row in rows}


def test_read_ignore_file_absent_is_a_noop(tmp_path):
    assert read_ignore_file(tmp_path) == []


def test_read_ignore_file_skips_comments_and_blank_lines(tmp_path):
    write(tmp_path, ".codefaunacodexignore", "# a comment\n\n  generated/*  \n\n#another\nvendored/*\n")
    assert read_ignore_file(tmp_path) == ["generated/*", "vendored/*"]


def test_build_codex_excludes_files_matched_by_codefaunacodexignore(tmp_path):
    write(tmp_path, "keep.py", "def kept():\n    pass\n")
    write(tmp_path, "generated/mod.py", "def generated_symbol():\n    pass\n")
    write(tmp_path, ".codefaunacodexignore", "generated/*\n")

    codex = build_codex(tmp_path)

    assert "kept" in _names(codex)
    assert "generated_symbol" not in _names(codex)
    assert set(codex["files"]) == {"keep.py"}


def test_codefaunacodexignore_is_a_union_with_the_ignore_argument(tmp_path):
    write(tmp_path, "keep.py", "def kept():\n    pass\n")
    write(tmp_path, "generated/mod.py", "def from_file():\n    pass\n")
    write(tmp_path, "cli_skipped/mod.py", "def from_argument():\n    pass\n")
    write(tmp_path, ".codefaunacodexignore", "generated/*\n")

    codex = build_codex(tmp_path, ignore_globs=["cli_skipped/*"])

    # Neither source wins over the other — both exclusions apply.
    assert _names(codex) == {"kept"}


def test_codefaunacodexignore_also_excludes_edges_for_the_ignored_file(tmp_path):
    write(tmp_path, "keep.py", "def kept():\n    import os\n    os.getcwd()\n")
    write(tmp_path, "generated/mod.py", "import json\n\n\ndef gen():\n    json.dumps({})\n")
    write(tmp_path, ".codefaunacodexignore", "generated/*\n")

    edges = build_codex(tmp_path)["edges"]

    assert set(edges["imports"]) == {"keep.py"}
    assert {row["file"] for row in edges["calls"]} == {"keep.py"}
