"""Tests — code_fauna_codex.diff. Pure offline JSON comparison, zero network."""
from __future__ import annotations

import json

from code_fauna_codex.diff import CodexDiff, cmd_diff, diff_codexes
from code_fauna_codex.index_store import CODEX_SCHEMA_VERSION, save_json


def make_codex(files: dict[str, str] | None = None,
               symbols: dict[str, list[dict]] | None = None,
               **extra) -> dict:
    """Minimal well-formed codex. `extra` lets a test bolt on unknown top-level keys."""
    return {
        "root": "/repo",
        "schema_version": CODEX_SCHEMA_VERSION,
        "files": files if files is not None else {},
        "symbols": symbols if symbols is not None else {},
        **extra,
    }


def symbol(name: str, file: str = "mod.py", line: int = 1, signature: str = "") -> dict:
    return {"name": name, "file": file, "line": line,
            "signature": signature or f"def {name}()", "docstring": "", "language": "python"}


BASE = make_codex(
    files={"mod.py": "hash-a"},
    symbols={"python_functions": [symbol("foo", line=1), symbol("bar", line=10)]},
)


# --- pure comparison -------------------------------------------------------------

def test_identical_codexes_report_no_change():
    result = diff_codexes(BASE, BASE)
    assert result.summary() == {
        "files_added": 0, "files_removed": 0, "files_changed": 0,
        "symbols_added": 0, "symbols_removed": 0, "symbols_moved": 0,
        "symbols_signature_changed": 0,
    }
    assert result.total_changes == 0


def test_added_symbol():
    new = make_codex(files={"mod.py": "hash-a"}, symbols={"python_functions": [
        symbol("foo", line=1), symbol("bar", line=10), symbol("baz", line=20)]})
    result = diff_codexes(BASE, new)
    assert result.summary()["symbols_added"] == 1
    assert result.symbols_added[0]["name"] == "baz"
    assert result.symbols_added[0]["key"] == "python_functions::baz::mod.py"
    assert result.symbols_added[0]["line"] == 20
    assert result.symbols_removed == []


def test_removed_symbol():
    new = make_codex(files={"mod.py": "hash-a"},
                     symbols={"python_functions": [symbol("foo", line=1)]})
    result = diff_codexes(BASE, new)
    assert result.summary()["symbols_removed"] == 1
    assert result.symbols_removed[0]["name"] == "bar"
    assert result.symbols_added == []


def test_moved_symbol_is_a_move_not_add_plus_remove():
    new = make_codex(files={"mod.py": "hash-b"}, symbols={"python_functions": [
        symbol("foo", line=1), symbol("bar", line=42)]})
    result = diff_codexes(BASE, new)
    assert result.summary()["symbols_moved"] == 1
    assert result.summary()["symbols_added"] == 0
    assert result.summary()["symbols_removed"] == 0
    assert result.summary()["symbols_signature_changed"] == 0
    moved = result.symbols_moved[0]
    assert (moved["name"], moved["old_line"], moved["new_line"]) == ("bar", 10, 42)


def test_signature_change_at_same_line():
    new = make_codex(files={"mod.py": "hash-b"}, symbols={"python_functions": [
        symbol("foo", line=1), symbol("bar", line=10, signature="def bar(x, y)")]})
    result = diff_codexes(BASE, new)
    assert result.summary()["symbols_signature_changed"] == 1
    assert result.summary()["symbols_moved"] == 0
    changed = result.symbols_signature_changed[0]
    assert changed["old_signature"] == "def bar()"
    assert changed["new_signature"] == "def bar(x, y)"


def test_symbol_moved_and_resigned_is_reported_in_both_categories():
    new = make_codex(files={"mod.py": "hash-b"}, symbols={"python_functions": [
        symbol("foo", line=1), symbol("bar", line=99, signature="def bar(z)")]})
    result = diff_codexes(BASE, new)
    assert result.summary()["symbols_moved"] == 1
    assert result.summary()["symbols_signature_changed"] == 1


def test_same_name_in_two_files_are_two_distinct_symbols():
    new = make_codex(files={"mod.py": "hash-a", "other.py": "hash-c"},
                     symbols={"python_functions": [
                         symbol("foo", line=1), symbol("bar", line=10),
                         symbol("foo", file="other.py", line=3)]})
    result = diff_codexes(BASE, new)
    assert result.summary()["symbols_added"] == 1
    assert result.symbols_added[0]["file"] == "other.py"


def test_changed_file_hash():
    new = make_codex(files={"mod.py": "hash-CHANGED"}, symbols=BASE["symbols"])
    result = diff_codexes(BASE, new)
    assert result.files_changed == ["mod.py"]
    assert result.files_added == []
    assert result.files_removed == []


def test_added_and_removed_files():
    new = make_codex(files={"other.py": "hash-c"}, symbols={})
    result = diff_codexes(BASE, new)
    assert result.files_added == ["other.py"]
    assert result.files_removed == ["mod.py"]
    assert result.files_changed == []


def test_unknown_top_level_keys_are_ignored():
    """Another agent adds `edges`; diff must work with or without it."""
    old = make_codex(files={"mod.py": "hash-a"}, symbols=BASE["symbols"],
                     edges=[{"from": "a", "to": "b"}], future_key=123)
    new = make_codex(files={"mod.py": "hash-a"}, symbols=BASE["symbols"],
                     edges=[{"from": "x", "to": "y"}])
    assert diff_codexes(old, new).total_changes == 0


def test_missing_files_or_symbols_keys_do_not_crash():
    result = diff_codexes({"schema_version": CODEX_SCHEMA_VERSION}, BASE)
    assert result.summary()["files_added"] == 1
    assert result.summary()["symbols_added"] == 2


def test_empty_diff_dataclass_defaults():
    assert CodexDiff().total_changes == 0


# --- cmd_diff: exit codes, human output, JSON envelope ---------------------------

def _write_pair(tmp_path, old: dict, new: dict):
    old_path, new_path = tmp_path / "old.json", tmp_path / "new.json"
    save_json(old_path, old)
    save_json(new_path, new)
    return old_path, new_path


def test_cmd_diff_identical_exits_zero_and_says_no_differences(tmp_path, capsys):
    old_path, new_path = _write_pair(tmp_path, BASE, BASE)
    code = cmd_diff(old_path, new_path)
    out = capsys.readouterr().out
    assert code == 0
    assert "No differences." in out


def test_cmd_diff_with_differences_still_exits_zero(tmp_path, capsys):
    """diff is a report, not a gate: differences must not change the exit code."""
    new = make_codex(files={"mod.py": "hash-b"},
                     symbols={"python_functions": [symbol("foo", line=1)]})
    old_path, new_path = _write_pair(tmp_path, BASE, new)
    code = cmd_diff(old_path, new_path)
    out = capsys.readouterr().out
    assert code == 0
    assert "bar" in out
    assert "symbols removed" in out


def test_cmd_diff_json_envelope_and_summary(tmp_path, capsys):
    new = make_codex(files={"mod.py": "hash-b", "new.py": "hash-n"},
                     symbols={"python_functions": [
                         symbol("foo", line=1),
                         symbol("bar", line=11, signature="def bar(x)")]})
    old_path, new_path = _write_pair(tmp_path, BASE, new)
    code = cmd_diff(old_path, new_path, as_json=True)
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["command"] == "diff"
    assert payload["ok"] is True
    assert payload["schema_version"] == 1
    assert payload["summary"] == {
        "files_added": 1, "files_removed": 0, "files_changed": 1,
        "symbols_added": 0, "symbols_removed": 0, "symbols_moved": 1,
        "symbols_signature_changed": 1,
    }
    assert payload["files"]["added"] == ["new.py"]
    assert payload["files"]["changed"] == ["mod.py"]
    assert payload["symbols"]["moved"][0]["new_line"] == 11
    assert payload["symbols"]["signature_changed"][0]["new_signature"] == "def bar(x)"


def test_cmd_diff_json_payload_is_never_truncated(tmp_path, capsys):
    """Human output is capped per group; the JSON lists always carry everything."""
    many = {"python_functions": [symbol(f"f{i}", line=i + 1) for i in range(60)]}
    old_path, new_path = _write_pair(tmp_path, make_codex(), make_codex(symbols=many))

    cmd_diff(old_path, new_path)
    human = capsys.readouterr().out
    assert "human output capped at" in human

    cmd_diff(old_path, new_path, as_json=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["symbols_added"] == 60
    assert len(payload["symbols"]["added"]) == 60


def test_cmd_diff_missing_old_path_exits_two_and_names_it(tmp_path, capsys):
    new_path = tmp_path / "new.json"
    save_json(new_path, BASE)
    missing = tmp_path / "absent_old.json"

    code = cmd_diff(missing, new_path)
    captured = capsys.readouterr()
    assert code == 2
    assert "absent_old.json" in captured.err


def test_cmd_diff_missing_new_path_exits_two_and_names_it(tmp_path, capsys):
    old_path = tmp_path / "old.json"
    save_json(old_path, BASE)
    missing = tmp_path / "absent_new.json"

    code = cmd_diff(old_path, missing, as_json=True)
    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload["ok"] is False
    assert "absent_new.json" in payload["error"]


def test_cmd_diff_codex_without_schema_version_exits_two(tmp_path, capsys):
    legacy = {"root": "/repo", "files": {"mod.py": "hash-a"}, "symbols": {}}
    old_path, new_path = _write_pair(tmp_path, legacy, BASE)

    code = cmd_diff(old_path, new_path)
    captured = capsys.readouterr()
    assert code == 2
    assert "old.json" in captured.err
    assert "scan" in captured.err


def test_cmd_diff_codex_with_newer_schema_version_exits_two(tmp_path, capsys):
    future = make_codex()
    future["schema_version"] = CODEX_SCHEMA_VERSION + 99
    old_path, new_path = _write_pair(tmp_path, BASE, future)

    code = cmd_diff(old_path, new_path, as_json=True)
    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload["ok"] is False
    assert "newer code-fauna-codex" in payload["error"]
