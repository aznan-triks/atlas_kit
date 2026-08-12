"""Provider registry — add a provider by adding one file + one entry here."""
from __future__ import annotations

from .base import EmbeddingError, EmbeddingProvider, EmbedRequest, InvalidApiKey, QuotaExhausted
from .gemini import GeminiProvider
from .openai import OpenAIProvider

PROVIDERS: dict[str, EmbeddingProvider] = {
    "gemini": GeminiProvider(),
    "openai": OpenAIProvider(),
}


def get_provider(name: str) -> EmbeddingProvider:
    try:
        return PROVIDERS[name]
    except KeyError:
        raise ValueError(f"Unknown provider '{name}'. Available: {', '.join(sorted(PROVIDERS))}") from None
