"""Tests — the `--json` envelope of the four semantic commands, plus the two
fail-fast compatibility guards every reader now applies (index key schema, atlas
schema). No network: the provider's HTTP hook is monkeypatched, exactly as in
tests/test_cli_semantic.py.

Two invariants are asserted everywhere: JSON mode puts EXACTLY ONE object on stdout
(so `json.loads(out)` on the whole stream is itself the assertion), and an error in
JSON mode is an `ok: false` envelope on stdout, never a stderr line.
"""
from __future__ import annotations

import json

import pytest

from conftest import write

from fauna_codex.cli import main
from fauna_codex.index_store import ATLAS_SCHEMA_VERSION
from fauna_codex.semantic import (
    CURRENT_KEY_SCHEMA, cmd_embed, cmd_search, cmd_similar, cmd_status,
)


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


@pytest.fixture
def fake_gemini(monkeypatch):
    """Every embed/search call answers [1.0, 0.0] — deterministic, offline."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

    def fake_post(url, headers, json_body, timeout):
        n = len(json_body["requests"])
        return _FakeResp(200, {"embeddings": [{"values": [1.0, 0.0]}] * n})

    import fauna_codex.providers.gemini as gemini_mod
    monkeypatch.setattr(gemini_mod, "_default_http_post", fake_post)


@pytest.fixture
def atlas_path(tmp_path):
    write(tmp_path, "jobs.py", 'def cancel_job(job_id):\n    """Cancel a running job."""\n    pass\n')
    path = tmp_path / "atlas.json"
    main(["scan", str(tmp_path), "--out", str(path)])
    return path


def _write_index(tmp_path, *, key_schema=CURRENT_KEY_SCHEMA, centroid=None):
    """Three 2-D entries, zero centroid (so recentring is the identity). `key_schema`
    is a parameter because the compatibility guard is exactly what several tests probe."""
    index = {
        "model": "m", "dim": 2, "centroid": [0.0, 0.0] if centroid is None else centroid,
        "entries": {
            "python_functions::cancel_job::jobs.py::10": {
                "section": "python_functions", "name": "cancel_job", "file": "jobs.py",
                "line": 10, "signature": "def cancel_job(job_id)",
                "docstring": "Cancel a running job.", "hash": "h1", "vector": [1.0, 0.0],
            },
            "python_functions::start_job::jobs.py::20": {
                "section": "python_functions", "name": "start_job", "file": "jobs.py",
                "line": 20, "signature": "def start_job(job_id)",
                "docstring": "Start a job.", "hash": "h2", "vector": [0.9, 0.4358898943540674],
            },
            "python_classes::Unrelated::other.py::3": {
                "section": "python_classes", "name": "Unrelated", "file": "other.py",
                "line": 3, "signature": "class Unrelated", "docstring": "",
                "hash": "h3", "vector": [0.0, 1.0],
            },
        },
    }
    if key_schema is not None:
        index["key_schema"] = key_schema
    path = tmp_path / "semantic_index.json"
    path.write_text(json.dumps(index), encoding="utf-8")
    return path


def _write_atlas(tmp_path, *, schema_version=ATLAS_SCHEMA_VERSION):
    atlas = {"root": str(tmp_path), "files": {}, "symbols": {"python_functions": [
        {"name": "cancel_job", "file": "jobs.py", "line": 10,
         "signature": "def cancel_job(job_id)", "docstring": "Cancel a running job."},
    ]}}
    if schema_version is not None:
        atlas["schema_version"] = schema_version
    path = tmp_path / "atlas.json"
    path.write_text(json.dumps(atlas), encoding="utf-8")
    return path


def _payload(capsys):
    """The whole of stdout must parse as ONE JSON object — that is the contract."""
    out = capsys.readouterr().out
    return json.loads(out)


# --- embed ------------------------------------------------------------------

def test_embed_json_envelope_carries_the_documented_keys(atlas_path, tmp_path, fake_gemini, capsys):
    index_path = tmp_path / "semantic_index.json"
    capsys.readouterr()
    code = cmd_embed(atlas_path, index_path, "gemini", None, None, 50, 5.0, as_json=True)
    payload = _payload(capsys)

    assert code == 0
    assert payload["ok"] is True
    assert payload["command"] == "embed"
    assert payload["atlas"] == str(atlas_path)
    assert payload["index"] == str(index_path)
    assert payload["provider"] == "gemini"
    assert isinstance(payload["model"], str) and payload["model"]
    assert isinstance(payload["dim"], int)
    assert payload["entries_total"] == 1
    assert payload["entries_indexed"] == 1
    assert payload["pruned"] == 0
    assert payload["migrated"] == 0


def test_embed_json_mode_prints_no_progress_lines(atlas_path, tmp_path, fake_gemini, capsys):
    """Batch progress ("  1/1") is human noise; in JSON mode it would break the
    one-object-on-stdout contract."""
    capsys.readouterr()
    cmd_embed(atlas_path, tmp_path / "idx.json", "gemini", None, None, 1, 5.0, as_json=True)
    out = capsys.readouterr().out
    assert out.count("\n{") == 0  # a single top-level object, nothing before it
    assert "1/1" not in out


def test_embed_json_error_when_atlas_missing(tmp_path, fake_gemini, capsys):
    code = cmd_embed(tmp_path / "nope.json", tmp_path / "idx.json", "gemini", None, None,
                     50, 5.0, as_json=True)
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 2
    assert payload["ok"] is False
    assert payload["command"] == "embed"
    assert "Atlas not found" in payload["error"]
    assert captured.err == ""  # JSON mode keeps stdout the only channel to read


def test_embed_refuses_an_atlas_with_an_unreadable_schema(tmp_path, fake_gemini, capsys):
    """Regression: a stale-schema atlas used to be trusted, and the prune step then
    deleted every index entry it could not find in it."""
    atlas = _write_atlas(tmp_path, schema_version=None)  # pre-schema_version atlas
    index_path = _write_index(tmp_path)
    code = cmd_embed(atlas, index_path, "gemini", None, None, 50, 5.0)
    err = capsys.readouterr().err
    assert code == 2
    assert "scan" in err

    # ...and the index it refused to compare against is untouched, not pruned.
    stored = json.loads(index_path.read_text(encoding="utf-8"))
    assert len(stored["entries"]) == 3


# --- search -----------------------------------------------------------------

def test_search_json_envelope_carries_the_documented_keys(tmp_path, fake_gemini, capsys):
    index_path = _write_index(tmp_path)
    capsys.readouterr()
    code = cmd_search("cancel a running job", index_path, "gemini", "m", 2, 8, 1.0,
                      None, 5.0, as_json=True)
    payload = _payload(capsys)

    assert code == 0
    assert payload["ok"] is True
    assert payload["command"] == "search"
    assert payload["question"] == "cancel a running job"
    assert payload["count"] == len(payload["results"])
    # 3 entries < MIN_ENTRIES_FOR_ZSCORE, so the relative gate never ran.
    assert payload["threshold_applied"] is False
    assert payload["results"]
    for result in payload["results"]:
        assert set(result) == {"score", "section", "name", "file", "line", "signature",
                               "docstring"}
    assert payload["results"][0]["name"] == "cancel_job"


def test_search_json_empty_index_is_an_error_envelope(tmp_path, fake_gemini, capsys):
    code = cmd_search("anything", tmp_path / "missing.json", "gemini", "m", 2, 8, 1.0,
                      None, 5.0, as_json=True)
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 1
    assert payload["ok"] is False
    assert "Index is empty" in payload["error"]
    assert captured.err == ""


# --- similar ----------------------------------------------------------------

def test_similar_json_envelope_carries_the_documented_keys(tmp_path, capsys):
    index_path = _write_index(tmp_path)
    code = cmd_similar(index_path, 1.0, None, False, as_json=True)
    payload = _payload(capsys)

    assert code == 0
    assert payload["ok"] is True
    assert payload["command"] == "similar"
    assert payload["entries"] == 3
    assert payload["pairs_considered"] == 3
    assert payload["excluded_same_file"] is False
    assert payload["same_file_pairs"] == 1  # cancel_job / start_job both live in jobs.py
    assert payload["count"] == len(payload["pairs"])
    for pair in payload["pairs"]:
        assert set(pair) == {"score", "a", "b"}
        for side in ("a", "b"):
            assert set(pair[side]) == {"section", "name", "file", "line"}
            assert "vector" not in pair[side]


def test_similar_json_reports_the_exclusion_flag(tmp_path, capsys):
    index_path = _write_index(tmp_path)
    code = cmd_similar(index_path, 1.0, None, True, as_json=True)
    payload = _payload(capsys)
    assert code == 0
    assert payload["excluded_same_file"] is True
    assert all(pair["a"]["file"] != pair["b"]["file"] for pair in payload["pairs"])


# --- status -----------------------------------------------------------------

def test_status_json_envelope_carries_the_documented_keys(tmp_path, capsys):
    atlas = _write_atlas(tmp_path)
    index_path = _write_index(tmp_path)
    code = cmd_status(atlas, index_path, as_json=True)
    payload = _payload(capsys)

    assert code == 0
    assert payload["ok"] is True
    assert payload["command"] == "status"
    assert payload["atlas"] == {"path": str(atlas), "resources": 1,
                                "schema_version": ATLAS_SCHEMA_VERSION}
    assert payload["index"] == {"path": str(index_path), "resources": 3, "model": "m",
                                "dim": 2, "key_schema": CURRENT_KEY_SCHEMA, "stale": 1}


def test_status_json_error_when_atlas_missing(tmp_path, capsys):
    code = cmd_status(tmp_path / "nope.json", tmp_path / "idx.json", as_json=True)
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 2
    assert payload["ok"] is False
    assert payload["command"] == "status"
    assert "Atlas not found" in payload["error"]
    assert captured.err == ""


def test_status_refuses_an_atlas_with_an_unreadable_schema(tmp_path, capsys):
    atlas = _write_atlas(tmp_path, schema_version=ATLAS_SCHEMA_VERSION + 1)
    code = cmd_status(atlas, _write_index(tmp_path))
    err = capsys.readouterr().err
    assert code == 2
    assert "newer fauna-codex" in err


def test_status_still_reports_a_missing_index(tmp_path, capsys):
    """The key-schema guard must not turn "no index yet" into a hard error — an index
    with no entries carries no keys to misread."""
    code = cmd_status(_write_atlas(tmp_path), tmp_path / "not_created_yet.json", as_json=True)
    payload = _payload(capsys)
    assert code == 0
    assert payload["index"]["resources"] == 0
    assert payload["index"]["key_schema"] == 0


# --- index key-schema guard, on all three readers ---------------------------

def test_search_refuses_an_index_written_under_an_older_key_schema(tmp_path, fake_gemini, capsys):
    index_path = _write_index(tmp_path, key_schema=None)  # absent field == schema 1
    code = cmd_search("anything", index_path, "gemini", "m", 2, 8, 1.0, None, 5.0)
    err = capsys.readouterr().err
    assert code == 2
    assert "key schema 1" in err and "embed" in err


def test_similar_refuses_an_index_written_under_an_older_key_schema(tmp_path, capsys):
    index_path = _write_index(tmp_path, key_schema=1)
    code = cmd_similar(index_path, 1.0, None, False)
    err = capsys.readouterr().err
    assert code == 2
    assert "key schema 1" in err and "embed" in err


def test_status_refuses_an_index_written_under_an_older_key_schema(tmp_path, capsys):
    index_path = _write_index(tmp_path, key_schema=None)
    code = cmd_status(_write_atlas(tmp_path), index_path)
    err = capsys.readouterr().err
    assert code == 2
    assert "key schema 1" in err and "embed" in err


def test_readers_refuse_an_index_written_by_a_newer_fauna_codex(tmp_path, capsys):
    index_path = _write_index(tmp_path, key_schema=CURRENT_KEY_SCHEMA + 1)
    code = cmd_similar(index_path, 1.0, None, False)
    err = capsys.readouterr().err
    assert code == 2
    assert "newer fauna-codex" in err


def test_key_schema_guard_reports_as_json_when_asked(tmp_path, capsys):
    index_path = _write_index(tmp_path, key_schema=1)
    code = cmd_similar(index_path, 1.0, None, False, as_json=True)
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 2
    assert payload["ok"] is False
    assert payload["command"] == "similar"
    assert "key schema 1" in payload["error"]
    assert captured.err == ""
