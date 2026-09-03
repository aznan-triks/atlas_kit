"""Tests — code_fauna_codex.cli, mechanical subcommands only (scan/find/section)."""
from __future__ import annotations

import json

from conftest import write

from code_fauna_codex.cli import main


def test_scan_then_find_roundtrip(tmp_path, capsys):
    write(tmp_path, "mod.py", 'def hello_world():\n    """Greets."""\n    pass\n')
    codex_path = tmp_path / "codex.json"

    code = main(["scan", str(tmp_path), "--out", str(codex_path)])
    assert code == 0
    assert codex_path.exists()

    capsys.readouterr()
    code = main(["find", "hello", "--codex", str(codex_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "hello_world" in out
    assert "mod.py:1" in out


def test_find_no_match_prints_clear_message_and_exits_zero(tmp_path, capsys):
    write(tmp_path, "mod.py", "def foo():\n    pass\n")
    codex_path = tmp_path / "codex.json"
    main(["scan", str(tmp_path), "--out", str(codex_path)])

    capsys.readouterr()
    code = main(["find", "zzz_nonexistent_xyz", "--codex", str(codex_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "no resource matches" in out.lower()


def test_section_dumps_json(tmp_path, capsys):
    write(tmp_path, "mod.py", "def foo():\n    pass\n")
    codex_path = tmp_path / "codex.json"
    main(["scan", str(tmp_path), "--out", str(codex_path)])

    capsys.readouterr()
    code = main(["section", "python_functions", "--codex", str(codex_path)])
    out = capsys.readouterr().out
    assert code == 0
    rows = json.loads(out)
    assert any(r["name"] == "foo" for r in rows)


def test_scan_respects_custom_ignore_glob(tmp_path):
    write(tmp_path, "keep.py", "def kept():\n    pass\n")
    write(tmp_path, "skip_me/mod.py", "def skipped():\n    pass\n")
    codex_path = tmp_path / "codex.json"

    main(["scan", str(tmp_path), "--out", str(codex_path), "--ignore", "skip_me/*"])
    data = json.loads(codex_path.read_text(encoding="utf-8"))
    names = {s["name"] for rows in data["symbols"].values() for s in rows}
    assert "kept" in names
    assert "skipped" not in names
