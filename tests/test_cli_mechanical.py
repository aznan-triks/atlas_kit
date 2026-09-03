"""Tests — fauna_codex.cli, mechanical subcommands only (scan/find/section)."""
from __future__ import annotations

import json

from conftest import write

from fauna_codex.cli import main


def test_scan_then_find_roundtrip(tmp_path, capsys):
    write(tmp_path, "mod.py", 'def hello_world():\n    """Greets."""\n    pass\n')
    atlas_path = tmp_path / "atlas.json"

    code = main(["scan", str(tmp_path), "--out", str(atlas_path)])
    assert code == 0
    assert atlas_path.exists()

    capsys.readouterr()
    code = main(["find", "hello", "--atlas", str(atlas_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "hello_world" in out
    assert "mod.py:1" in out


def test_find_no_match_prints_clear_message_and_exits_zero(tmp_path, capsys):
    write(tmp_path, "mod.py", "def foo():\n    pass\n")
    atlas_path = tmp_path / "atlas.json"
    main(["scan", str(tmp_path), "--out", str(atlas_path)])

    capsys.readouterr()
    code = main(["find", "zzz_nonexistent_xyz", "--atlas", str(atlas_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "no resource matches" in out.lower()


def test_section_dumps_json(tmp_path, capsys):
    write(tmp_path, "mod.py", "def foo():\n    pass\n")
    atlas_path = tmp_path / "atlas.json"
    main(["scan", str(tmp_path), "--out", str(atlas_path)])

    capsys.readouterr()
    code = main(["section", "python_functions", "--atlas", str(atlas_path)])
    out = capsys.readouterr().out
    assert code == 0
    rows = json.loads(out)
    assert any(r["name"] == "foo" for r in rows)


def test_scan_respects_custom_ignore_glob(tmp_path):
    write(tmp_path, "keep.py", "def kept():\n    pass\n")
    write(tmp_path, "skip_me/mod.py", "def skipped():\n    pass\n")
    atlas_path = tmp_path / "atlas.json"

    main(["scan", str(tmp_path), "--out", str(atlas_path), "--ignore", "skip_me/*"])
    data = json.loads(atlas_path.read_text(encoding="utf-8"))
    names = {s["name"] for rows in data["symbols"].values() for s in rows}
    assert "kept" in names
    assert "skipped" not in names
