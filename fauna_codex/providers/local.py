"""Local/offline embedding provider — fastembed (ONNX), no network, no API key.

Optional dependency: pip install 'fauna-codex[local]'. `_default_embed_fn` is the
injection point tests monkeypatch to stay dependency-free, mirroring how
gemini.py/openai.py inject `_default_http_post`.
"""
from __future__ import annotations

from .base import EmbedRequest, EmbeddingError, l2_normalize

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_DIMENSIONS = 384


def _default_embed_fn(model_name: str, texts: list[str]) -> list[list[float]]:
    try:
        from fastembed import TextEmbedding
    except ImportError as exc:
        raise EmbeddingError(
            "Local provider needs the 'fastembed' package: pip install 'fauna-codex[local]'."
        ) from exc
    model = TextEmbedding(model_name=model_name)
    return [list(vector) for vector in model.embed(texts)]


class LocalProvider:
    name = "local"
    default_model = DEFAULT_MODEL
    default_dimensions = DEFAULT_DIMENSIONS
    env_var = ""
    requires_api_key = False

    def embed(self, request: EmbedRequest, http_post=None) -> list[list[float]]:
        vectors = _default_embed_fn(request.model, request.texts)
        return [l2_normalize(vector) for vector in vectors]
