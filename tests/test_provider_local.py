"""Tests — code_fauna_codex.providers.local. No real fastembed call: _default_embed_fn is faked."""
from __future__ import annotations

import math
import sys

import pytest

from code_fauna_codex.providers.base import EmbeddingError, EmbedRequest
from code_fauna_codex.providers.local import LocalProvider


def _request(**overrides):
    base = dict(texts=["hello"], task_type="document", model="m", dimensions=4,
               api_key="", timeout_s=5.0)
    base.update(overrides)
    return EmbedRequest(**base)


def test_local_provider_requires_no_api_key():
    assert LocalProvider().requires_api_key is False
    assert LocalProvider().env_var == ""


def test_registry_includes_local_provider():
    from code_fauna_codex.providers import PROVIDERS, get_provider
    assert "local" in PROVIDERS
    assert isinstance(get_provider("local"), LocalProvider)


def test_local_provider_embed_l2_normalizes(monkeypatch):
    import code_fauna_codex.providers.local as local_mod

    def fake_embed_fn(model_name, texts):
        assert model_name == "m"
        return [[3.0, 4.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(local_mod, "_default_embed_fn", fake_embed_fn)
    vectors = LocalProvider().embed(_request())
    assert len(vectors) == 1
    assert math.isclose(sum(v * v for v in vectors[0]), 1.0, rel_tol=1e-6)


def test_local_provider_missing_dependency_raises_embedding_error(monkeypatch):
    # sys.modules[name] = None is the standard way to force the next `import name`
    # to raise ImportError, regardless of whether fastembed is actually installed
    # on the machine running the test.
    monkeypatch.setitem(sys.modules, "fastembed", None)
    with pytest.raises(EmbeddingError, match="fastembed"):
        LocalProvider().embed(_request())
