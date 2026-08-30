"""Google Generative Language API — batchEmbedContents."""
from __future__ import annotations

import requests

from .base import EmbeddingError, EmbedRequest, InvalidApiKey, QuotaExhausted, l2_normalize

DEFAULT_MODEL = "gemini-embedding-001"
DEFAULT_DIMENSIONS = 768
BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

_TASK_TYPE = {"document": "RETRIEVAL_DOCUMENT", "query": "RETRIEVAL_QUERY"}


def _default_http_post(url, headers, json_body, timeout):
    return requests.post(url, headers=headers, json=json_body, timeout=timeout)


class GeminiProvider:
    name = "gemini"
    default_model = DEFAULT_MODEL
    default_dimensions = DEFAULT_DIMENSIONS
    env_var = "GEMINI_API_KEY"
    requires_api_key = True

    def embed(self, request: EmbedRequest, http_post=None) -> list[list[float]]:
        post = http_post or _default_http_post
        url = f"{BASE_URL}/models/{request.model}:batchEmbedContents"
        body = {
            "requests": [
                {
                    "model": f"models/{request.model}",
                    "content": {"parts": [{"text": t}]},
                    "taskType": _TASK_TYPE[request.task_type],
                    "outputDimensionality": request.dimensions,
                }
                for t in request.texts
            ]
        }
        resp = post(url, {"x-goog-api-key": request.api_key, "content-type": "application/json"},
                    body, request.timeout_s)

        if resp.status_code == 200:
            return [l2_normalize(e["values"]) for e in resp.json()["embeddings"]]
        if resp.status_code == 429:
            raise QuotaExhausted("Gemini quota exceeded (HTTP 429).")
        if resp.status_code in (400, 401, 403):
            detail = (resp.json().get("error") or {}).get("message", f"HTTP {resp.status_code}")
            raise InvalidApiKey(detail)
        raise EmbeddingError(f"Unexpected Gemini response (HTTP {resp.status_code}): {resp.text[:300]}")
