"""Tests — fauna_codex.providers. No test touches the network: http_post is a fake."""
from __future__ import annotations

import math

import pytest

from fauna_codex.providers import PROVIDERS, get_provider
from fauna_codex.providers.base import EmbedRequest, InvalidApiKey, QuotaExhausted
from fauna_codex.providers.gemini import GeminiProvider
from fauna_codex.providers.openai import OpenAIProvider


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def _request(**overrides):
    base = dict(texts=["hello"], task_type="document", model="m", dimensions=4,
               api_key="secret", timeout_s=5.0)
    base.update(overrides)
    return EmbedRequest(**base)


def test_registry_exposes_gemini_openai_and_local():
    assert set(PROVIDERS) == {"gemini", "openai", "local"}
    assert isinstance(PROVIDERS["gemini"], GeminiProvider)
    assert isinstance(PROVIDERS["openai"], OpenAIProvider)


def test_get_provider_unknown_name_raises():
    with pytest.raises(ValueError, match="Unknown provider"):
        get_provider("does-not-exist")


def test_gemini_embed_success_and_l2_normalized():
    def fake_post(url, headers, json_body, timeout):
        assert "batchEmbedContents" in url
        assert headers["x-goog-api-key"] == "secret"
        return FakeResponse(200, {"embeddings": [{"values": [3.0, 4.0, 0.0, 0.0]}]})

    vectors = GeminiProvider().embed(_request(), http_post=fake_post)
    assert len(vectors) == 1
    assert math.isclose(sum(v * v for v in vectors[0]), 1.0, rel_tol=1e-6)


def test_gemini_embed_quota_raises():
    def fake_post(url, headers, json_body, timeout):
        return FakeResponse(429, {})

    with pytest.raises(QuotaExhausted):
        GeminiProvider().embed(_request(), http_post=fake_post)


def test_gemini_embed_invalid_key_raises():
    def fake_post(url, headers, json_body, timeout):
        return FakeResponse(401, {"error": {"message": "bad key"}})

    with pytest.raises(InvalidApiKey, match="bad key"):
        GeminiProvider().embed(_request(), http_post=fake_post)


def test_openai_embed_success_and_l2_normalized():
    def fake_post(url, headers, json_body, timeout):
        assert url.endswith("/embeddings")
        assert headers["Authorization"] == "Bearer secret"
        assert json_body["dimensions"] == 4
        return FakeResponse(200, {"data": [{"embedding": [1.0, 0.0, 0.0, 0.0]}]})

    vectors = OpenAIProvider().embed(_request(), http_post=fake_post)
    assert len(vectors) == 1
    assert math.isclose(sum(v * v for v in vectors[0]), 1.0, rel_tol=1e-6)


def test_openai_embed_quota_raises():
    def fake_post(url, headers, json_body, timeout):
        return FakeResponse(429, {})

    with pytest.raises(QuotaExhausted):
        OpenAIProvider().embed(_request(), http_post=fake_post)


def test_openai_embed_invalid_key_raises():
    def fake_post(url, headers, json_body, timeout):
        return FakeResponse(401, {"error": {"message": "invalid key"}})

    with pytest.raises(InvalidApiKey, match="invalid key"):
        OpenAIProvider().embed(_request(), http_post=fake_post)
