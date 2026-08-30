"""Shared contract every embedding provider implements. Zero provider-specific code here."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


class EmbeddingError(RuntimeError):
    """A provider call failed in a way the caller must react to."""


class QuotaExhausted(EmbeddingError):
    pass


class InvalidApiKey(EmbeddingError):
    pass


@dataclass
class EmbedRequest:
    texts: list[str]
    task_type: str  # "document" | "query" — normalized across providers
    model: str
    dimensions: int
    api_key: str
    timeout_s: float


HttpPost = Callable[[str, dict, dict, float], Any]


class EmbeddingProvider(Protocol):
    name: str
    default_model: str
    default_dimensions: int
    env_var: str
    requires_api_key: bool

    def embed(self, request: EmbedRequest, http_post: HttpPost | None = None) -> list[list[float]]: ...


def l2_normalize(vec: list[float]) -> list[float]:
    norm = sum(v * v for v in vec) ** 0.5
    if norm == 0.0:
        return list(vec)
    return [v / norm for v in vec]
