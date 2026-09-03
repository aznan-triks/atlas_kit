"""Tests — fauna_codex.cli semantic subcommands (embed/search/status). Network is faked."""
from __future__ import annotations

import json

import pytest

from conftest import write

from fauna_codex.cli import main


@pytest.fixture
def atlas_path(tmp_path):
    write(tmp_path, "jobs.py", 'def cancel_job(job_id):\n    """Cancel a running job."""\n    pass\n')
    path = tmp_path / "atlas.json"
    main(["scan", str(tmp_path), "--out", str(path)])
    return path


def test_embed_without_api_key_fails_loud(atlas_path, monkeypatch, capsys):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    code = main(["embed", "--atlas", str(atlas_path), "--provider", "gemini",
                "--index", "unused.json"])
    err = capsys.readouterr().err
    assert code != 0
    assert "GEMINI_API_KEY" in err


def test_embed_fails_loud_if_atlas_missing_instead_of_pruning_everything(monkeypatch, tmp_path, capsys):
    """Regression: `embed` without a valid --atlas used to silently treat the atlas as
    empty (load_json's missing-file fallback) and prune the ENTIRE existing index as
    stale, exiting 0. It must now refuse instead of guessing."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    missing_atlas = tmp_path / "does_not_exist.json"
    index_path = tmp_path / "semantic_index.json"
    index_path.write_text(json.dumps({
        "model": "gemini-embedding-001", "dim": 768, "key_schema": 2,
        "entries": {"python_functions::foo::mod.py::1": {
            "section": "python_functions", "name": "foo", "file": "mod.py", "line": 1,
            "signature": "", "docstring": "", "hash": "h", "vector": [1.0, 0.0],
        }},
    }), encoding="utf-8")

    code = main(["embed", "--atlas", str(missing_atlas), "--provider", "gemini",
                "--index", str(index_path)])
    err = capsys.readouterr().err
    assert code != 0
    assert "Atlas not found" in err

    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert "python_functions::foo::mod.py::1" in index["entries"]  # untouched, not pruned


def test_status_fails_loud_if_atlas_missing(tmp_path, capsys):
    missing_atlas = tmp_path / "does_not_exist.json"
    code = main(["status", "--atlas", str(missing_atlas), "--index", str(tmp_path / "idx.json")])
    err = capsys.readouterr().err
    assert code != 0
    assert "Atlas not found" in err


def test_embed_then_search_roundtrip(atlas_path, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    index_path = tmp_path / "semantic_index.json"

    def fake_post(url, headers, json_body, timeout):
        # Same vector for both the indexed document and the search query — makes
        # the roundtrip deterministic (cosine similarity 1.0) without a real model.
        n = len(json_body["requests"])
        return _FakeResp(200, {"embeddings": [{"values": [1.0, 0.0]}] * n})

    import fauna_codex.providers.gemini as gemini_mod
    monkeypatch.setattr(gemini_mod, "_default_http_post", fake_post)

    code = main(["embed", "--atlas", str(atlas_path), "--provider", "gemini",
                "--index", str(index_path)])
    assert code == 0
    assert index_path.exists()

    capsys.readouterr()
    code = main(["search", "cancel a running job", "--provider", "gemini",
                "--index", str(index_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "cancel_job" in out


def test_embed_rotates_to_next_key_on_quota_exhausted(atlas_path, monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEYS", "key-a, key-b")
    index_path = tmp_path / "semantic_index.json"
    calls = []

    def fake_post(url, headers, json_body, timeout):
        calls.append(headers["x-goog-api-key"])
        if headers["x-goog-api-key"] == "key-a":
            return _FakeResp(429, {})
        n = len(json_body["requests"])
        return _FakeResp(200, {"embeddings": [{"values": [1.0, 0.0]}] * n})

    import fauna_codex.providers.gemini as gemini_mod
    monkeypatch.setattr(gemini_mod, "_default_http_post", fake_post)

    code = main(["embed", "--atlas", str(atlas_path), "--provider", "gemini",
                "--index", str(index_path)])
    err = capsys.readouterr().err
    assert code == 0
    assert calls == ["key-a", "key-b"]
    # Fail Fast: rotation is printed (key identified by position, never by value).
    assert "Key 1/2 exhausted" in err
    assert "key-a" not in err and "key-b" not in err


def test_embed_does_not_rotate_past_invalid_key(atlas_path, monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEYS", "bad-key, good-key")
    index_path = tmp_path / "semantic_index.json"
    calls = []

    def fake_post(url, headers, json_body, timeout):
        calls.append(headers["x-goog-api-key"])
        return _FakeResp(401, {"error": {"message": "bad key"}})

    import fauna_codex.providers.gemini as gemini_mod
    monkeypatch.setattr(gemini_mod, "_default_http_post", fake_post)

    code = main(["embed", "--atlas", str(atlas_path), "--provider", "gemini",
                "--index", str(index_path)])
    err = capsys.readouterr().err
    assert code != 0
    assert "bad key" in err
    # A bad key is a config error, not a capacity one — Fail Fast, never rotated past.
    assert calls == ["bad-key"]


def test_embed_does_not_retry_exhausted_key_on_later_batches(monkeypatch, tmp_path, capsys):
    """Once key-a is exhausted, it must be dropped for good — not retried on every
    subsequent batch. batch_size=1 forces 2 separate _embed_with_rotation calls."""
    write(tmp_path, "jobs.py",
          'def cancel_job(job_id):\n    """Cancel a running job."""\n    pass\n\n'
          'def start_job(job_id):\n    """Start a job."""\n    pass\n')
    atlas_path = tmp_path / "atlas.json"
    main(["scan", str(tmp_path), "--out", str(atlas_path)])

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEYS", "key-a, key-b")
    index_path = tmp_path / "semantic_index.json"
    calls = []

    def fake_post(url, headers, json_body, timeout):
        key = headers["x-goog-api-key"]
        calls.append(key)
        if key == "key-a":
            return _FakeResp(429, {})
        n = len(json_body["requests"])
        return _FakeResp(200, {"embeddings": [{"values": [1.0, 0.0]}] * n})

    import fauna_codex.providers.gemini as gemini_mod
    monkeypatch.setattr(gemini_mod, "_default_http_post", fake_post)

    code = main(["embed", "--atlas", str(atlas_path), "--provider", "gemini",
                "--index", str(index_path), "--batch-size", "1"])
    assert code == 0
    # key-a tried exactly once (batch 1), never retried on batch 2 — sticky rotation.
    assert calls == ["key-a", "key-b", "key-b"]


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload
