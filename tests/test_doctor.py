"""`doctor` is an offline diagnostic: it must always exit 0, and it must NEVER let an
API key value reach stdout. The key-value assertions below are the security
regression test — if they ever fail, a key leaked into the report."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_fauna_codex.doctor import cmd_doctor
from code_fauna_codex.index_store import CODEX_SCHEMA_VERSION, save_json

SECRET = "sk-doctor-must-never-print-this"
SECRET_A = "sk-key-alpha-secret"
SECRET_B = "sk-key-bravo-secret"
SECRET_C = "sk-key-charlie-secret"


def _clear_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("GEMINI_API_KEY", "GEMINI_API_KEYS", "OPENAI_API_KEY", "OPENAI_API_KEYS"):
        monkeypatch.delenv(var, raising=False)


def _codex(tmp_path: Path) -> Path:
    path = tmp_path / "codex.json"
    save_json(path, {
        "schema_version": CODEX_SCHEMA_VERSION,
        "root": str(tmp_path),
        "files": {"a.py": "hash-a", "b.py": "hash-b"},
        "symbols": {"functions": [
            {"name": "alpha", "file": "a.py", "line": 1, "signature": "def alpha()",
             "docstring": "", "language": "python"},
            {"name": "bravo", "file": "b.py", "line": 2, "signature": "def bravo()",
             "docstring": "", "language": "python"},
        ]},
    })
    return path


def _index(tmp_path: Path) -> Path:
    path = tmp_path / "semantic_index.json"
    save_json(path, {
        "model": "text-embedding-004", "dim": 3, "key_schema": 2,
        "centroid": [0.0, 0.0, 1.0],
        "entries": {"functions::alpha::a.py::1": {
            "section": "functions", "name": "alpha", "file": "a.py", "line": 1,
            "hash": "h", "vector": [0.0, 0.0, 1.0],
        }},
    })
    return path


def test_no_key_configured_reports_zero(tmp_path, capsys, monkeypatch):
    _clear_keys(monkeypatch)
    assert cmd_doctor(tmp_path / "codex.json", tmp_path / "index.json") == 0
    out = capsys.readouterr().out
    assert "no key configured" in out
    assert "gemini" in out and "openai" in out


def test_singular_env_var_counts_one_and_never_prints_the_value(tmp_path, capsys, monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    assert cmd_doctor(tmp_path / "codex.json", tmp_path / "index.json") == 0
    out = capsys.readouterr().out
    assert "1 key(s) configured" in out
    assert SECRET not in out


def test_plural_env_var_counts_three_and_never_prints_any_value(tmp_path, capsys, monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEYS", f"{SECRET_A}, {SECRET_B},{SECRET_C}")
    assert cmd_doctor(tmp_path / "codex.json", tmp_path / "index.json") == 0
    out = capsys.readouterr().out
    assert "3 key(s) configured" in out
    for secret in (SECRET_A, SECRET_B, SECRET_C):
        assert secret not in out


def test_json_mode_never_prints_a_key_value_either(tmp_path, capsys, monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEYS", f"{SECRET_A},{SECRET_B}")
    assert cmd_doctor(tmp_path / "codex.json", tmp_path / "index.json", as_json=True) == 0
    out = capsys.readouterr().out
    assert SECRET_A not in out and SECRET_B not in out
    providers = {p["name"]: p for p in json.loads(out)["providers"]}
    assert providers["openai"]["keys_configured"] == 2
    assert providers["local"]["requires_api_key"] is False


def test_missing_codex_and_index_still_exit_zero(tmp_path, capsys, monkeypatch):
    _clear_keys(monkeypatch)
    assert cmd_doctor(tmp_path / "nope.json", tmp_path / "nada.json") == 0
    out = capsys.readouterr().out
    assert "MISSING" in out
    assert "code-fauna-codex scan" in out and "code-fauna-codex embed" in out


def test_real_codex_and_index_are_summarised(tmp_path, capsys, monkeypatch):
    _clear_keys(monkeypatch)
    assert cmd_doctor(_codex(tmp_path), _index(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "2 file(s), 2 symbol(s)" in out
    assert f"schema {CODEX_SCHEMA_VERSION}" in out
    assert "1 entrie(s)" in out
    assert "text-embedding-004" in out
    assert "centroid: present" in out


def test_json_mode_is_valid_json_with_ok_and_command(tmp_path, capsys, monkeypatch):
    _clear_keys(monkeypatch)
    assert cmd_doctor(_codex(tmp_path), _index(tmp_path), as_json=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "doctor"
    assert payload["files"]["codex"]["symbol_count"] == 2
    assert payload["files"]["codex"]["schema_version"] == CODEX_SCHEMA_VERSION
    assert payload["files"]["index"]["entry_count"] == 1
    assert payload["files"]["index"]["key_schema"] == 2
    assert payload["files"]["index"]["has_centroid"] is True
    assert payload["runtime"]["python_version"]
    assert ".py" in payload["parsers"]["backend_by_extension"]


def test_malformed_codex_is_reported_not_crashed(tmp_path, capsys, monkeypatch):
    _clear_keys(monkeypatch)
    broken = tmp_path / "codex.json"
    broken.write_text("{not json", encoding="utf-8")
    assert cmd_doctor(broken, tmp_path / "index.json") == 0
    assert "invalid JSON" in capsys.readouterr().out


def test_parser_backends_cover_every_supported_extension(tmp_path, capsys, monkeypatch):
    _clear_keys(monkeypatch)
    from code_fauna_codex.scan import SUPPORTED_EXTENSIONS

    assert cmd_doctor(tmp_path / "a.json", tmp_path / "i.json", as_json=True) == 0
    backends = json.loads(capsys.readouterr().out)["parsers"]["backend_by_extension"]
    assert set(backends) == SUPPORTED_EXTENSIONS
    assert backends[".py"].startswith("ast")
    # Which of the two a machine gets depends on installed grammars — both are valid.
    for extension in SUPPORTED_EXTENSIONS - {".py"}:
        assert backends[extension] in ("regex", "treesitter")


def test_grammar_packages_are_reported_with_their_pip_name(tmp_path, capsys, monkeypatch):
    _clear_keys(monkeypatch)
    assert cmd_doctor(tmp_path / "a.json", tmp_path / "i.json", as_json=True) == 0
    grammars = json.loads(capsys.readouterr().out)["parsers"]["grammars"]
    packages = {g["package"] for g in grammars}
    assert "tree-sitter-javascript" in packages
    assert all(g["package"].startswith("tree-sitter-") for g in grammars)
    assert all(g["extensions"] for g in grammars)
