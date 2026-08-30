"""Tests — atlas_kit.cli semantic subcommands (embed/search/status). Network is faked."""
from __future__ import annotations

import json

import pytest

from conftest import write

from atlas_kit.cli import main


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


def test_embed_then_search_roundtrip(atlas_path, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    index_path = tmp_path / "semantic_index.json"

    def fake_post(url, headers, json_body, timeout):
        # Same vector for both the indexed document and the search query — makes
        # the roundtrip deterministic (cosine similarity 1.0) without a real model.
        n = len(json_body["requests"])
        return _FakeResp(200, {"embeddings": [{"values": [1.0, 0.0]}] * n})

    import atlas_kit.providers.gemini as gemini_mod
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

    import atlas_kit.providers.gemini as gemini_mod
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

    import atlas_kit.providers.gemini as gemini_mod
    monkeypatch.setattr(gemini_mod, "_default_http_post", fake_post)

    code = main(["embed", "--atlas", str(atlas_path), "--provider", "gemini",
                "--index", str(index_path)])
    err = capsys.readouterr().err
    assert code != 0
    assert "bad key" in err
    # A bad key is a config error, not a capacity one — Fail Fast, never rotated past.
    assert calls == ["bad-key"]


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload
