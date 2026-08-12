"""OpenAI Embeddings API — POST /v1/embeddings."""
from __future__ import annotations

import requests

from .base import EmbeddingError, EmbedRequest, InvalidApiKey, QuotaExhausted, l2_normalize

DEFAULT_MODEL = "text-embedding-3-small"
DEFAULT_DIMENSIONS = 768
BASE_URL = "https://api.openai.com/v1"


def _default_http_post(url, headers, json_body, timeout):
    return requests.post(url, headers=headers, json=json_body, timeout=timeout)


class OpenAIProvider:
    name = "openai"
    default_model = DEFAULT_MODEL
    default_dimensions = DEFAULT_DIMENSIONS
    env_var = "OPENAI_API_KEY"

    def embed(self, request: EmbedRequest, http_post=None) -> list[list[float]]:
        post = http_post or _default_http_post
        url = f"{BASE_URL}/embeddings"
        body = {"model": request.model, "input": request.texts, "dimensions": request.dimensions}
        resp = post(url, {"Authorization": f"Bearer {request.api_key}",
                          "Content-Type": "application/json"}, body, request.timeout_s)

        if resp.status_code == 200:
            return [l2_normalize(row["embedding"]) for row in resp.json()["data"]]
        if resp.status_code == 429:
            raise QuotaExhausted("OpenAI quota exceeded (HTTP 429).")
        if resp.status_code in (400, 401, 403):
            detail = (resp.json().get("error") or {}).get("message", f"HTTP {resp.status_code}")
            raise InvalidApiKey(detail)
        raise EmbeddingError(f"Unexpected OpenAI response (HTTP {resp.status_code}): {resp.text[:300]}")
