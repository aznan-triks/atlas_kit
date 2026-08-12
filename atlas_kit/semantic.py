"""Semantic mode: build a vector index over the mechanical atlas, search it by meaning.

Provider-agnostic — this module never talks HTTP directly, it goes through
`providers.EmbeddingProvider`. Same incremental-hash doctrine as `scan.py`.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

from atlas_kit.index_store import load_json, save_json
from atlas_kit.providers import get_provider
from atlas_kit.providers.base import EmbedRequest, EmbeddingError, InvalidApiKey, QuotaExhausted

DEFAULT_BATCH_SIZE = 50
DEFAULT_TOP_K = 8
DEFAULT_MIN_SCORE = 0.55
DEFAULT_TIMEOUT_S = 60.0


def entry_key(section: str, row: dict) -> str:
    return f"{section}::{row.get('name', '')}::{row.get('file', '')}"


def entry_text(section: str, row: dict) -> str:
    return "\n".join([
        f"{section} : {row.get('name', '')}",
        row.get("signature", "") or "",
        (row.get("docstring") or "").strip(),
        f"file : {row.get('file', '')}",
    ])


def entry_hash(text: str, model: str, dim: int) -> str:
    return hashlib.sha256(f"{model}|{dim}|{text}".encode("utf-8")).hexdigest()


def iter_atlas_entries(atlas: dict, model: str = "", dim: int = 0) -> list[dict]:
    out = []
    for section, rows in (atlas.get("symbols") or {}).items():
        for row in rows:
            text = entry_text(section, row)
            out.append({
                "key": entry_key(section, row), "section": section, "name": row.get("name", ""),
                "file": row.get("file", ""), "line": row.get("line", 0),
                "signature": row.get("signature", ""), "docstring": (row.get("docstring") or "").strip(),
                "text": text, "hash": entry_hash(text, model, dim),
            })
    return out


def pending_entries(entries: list[dict], index: dict, model: str = "", dim: int = 0) -> list[dict]:
    if model and (index.get("model") != model or int(index.get("dim") or 0) != int(dim)):
        return list(entries)
    indexed = index.get("entries") or {}
    return [e for e in entries if (indexed.get(e["key"]) or {}).get("hash") != e["hash"]]


def cosine(a: list[float], b: list[float]) -> float:
    """Dot product — a true cosine similarity only holds because every provider's
    embed() returns L2-normalized vectors; a non-normalizing provider would break this."""
    return sum(x * y for x, y in zip(a, b))


def search(index: dict, query_vector: list[float], top_k: int, min_score: float,
          section: str | None = None) -> list[dict]:
    hits = []
    for key, row in (index.get("entries") or {}).items():
        if section and row.get("section") != section:
            continue
        score = cosine(query_vector, row.get("vector") or [])
        if score < min_score:
            continue
        hits.append({**row, "key": key, "score": score})
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:top_k]


def _resolve_api_key(provider) -> str | None:
    return os.environ.get(provider.env_var) or None


def cmd_embed(atlas_path: Path, index_path: Path, provider_name: str, model: str | None,
             dimensions: int | None, batch_size: int, timeout_s: float) -> int:
    provider = get_provider(provider_name)
    model = model or provider.default_model
    dim = dimensions or provider.default_dimensions

    api_key = _resolve_api_key(provider)
    if not api_key:
        print(f"Missing API key: set {provider.env_var} to use --provider {provider.name}.",
              file=sys.stderr)
        return 2

    atlas = load_json(atlas_path, {"symbols": {}})
    entries = iter_atlas_entries(atlas, model=model, dim=dim)
    index = load_json(index_path, {"model": "", "dim": 0, "entries": {}})
    todo = pending_entries(entries, index, model=model, dim=dim)
    if index.get("model") != model or int(index.get("dim") or 0) != dim:
        index = {"model": model, "dim": dim, "entries": {}}

    if not todo:
        print(f"0 new entries — index up to date ({len(entries)} resources).")
        return 0

    print(f"{len(todo)} entrie(s) to index out of {len(entries)} (batches of {batch_size})...")
    done = 0
    try:
        for start in range(0, len(todo), batch_size):
            chunk = todo[start:start + batch_size]
            request = EmbedRequest(texts=[e["text"] for e in chunk], task_type="document",
                                   model=model, dimensions=dim, api_key=api_key, timeout_s=timeout_s)
            vectors = provider.embed(request)
            for entry, vector in zip(chunk, vectors):
                index["entries"][entry["key"]] = {
                    "section": entry["section"], "name": entry["name"], "file": entry["file"],
                    "line": entry["line"], "signature": entry["signature"],
                    "docstring": entry["docstring"], "hash": entry["hash"], "vector": vector,
                }
            done += len(chunk)
            print(f"  {done}/{len(todo)}")
    except (QuotaExhausted, InvalidApiKey, EmbeddingError) as exc:
        save_json(index_path, index)
        print(f"\n{exc}", file=sys.stderr)
        return 2
    save_json(index_path, index)
    print(f"Index written: {index_path} ({len(index['entries'])} resources).")
    return 0


def cmd_search(question: str, index_path: Path, provider_name: str,
               model: str | None, dimensions: int | None, top_k: int, min_score: float,
               section: str | None, timeout_s: float) -> int:
    index = load_json(index_path, {"model": "", "dim": 0, "entries": {}})
    if not index.get("entries"):
        print("Index is empty — run `atlas-kit embed` first.", file=sys.stderr)
        return 1

    provider = get_provider(provider_name)
    model = model or provider.default_model
    dim = dimensions or provider.default_dimensions

    api_key = _resolve_api_key(provider)
    if not api_key:
        print(f"Missing API key: set {provider.env_var} to use --provider {provider.name}.",
              file=sys.stderr)
        return 2

    request = EmbedRequest(texts=[question], task_type="query", model=model, dimensions=dim,
                           api_key=api_key, timeout_s=timeout_s)
    try:
        query_vector = provider.embed(request)[0]
    except (QuotaExhausted, InvalidApiKey, EmbeddingError) as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2

    hits = search(index, query_vector, top_k=top_k, min_score=min_score, section=section)
    if not hits:
        print(f"No resource above threshold for '{question}' — nothing to reuse.")
        return 0

    current = ""
    for hit in hits:
        if hit["section"] != current:
            current = hit["section"]
            print(f"\n-- {current}")
        doc = hit.get("docstring") or ""
        suffix = f"  — {doc}" if doc else ""
        print(f"  [{hit['score']:.2f}] {hit['name']}  {hit['file']}:{hit['line']}{suffix}")
    print(f"\n{len(hits)} result(s).")
    return 0


def cmd_status(atlas_path: Path, index_path: Path) -> int:
    index = load_json(index_path, {"model": "", "dim": 0, "entries": {}})
    atlas = load_json(atlas_path, {"symbols": {}})
    entries = iter_atlas_entries(atlas, model=index.get("model", ""), dim=int(index.get("dim") or 0))
    indexed = index.get("entries") or {}
    stale = [e["key"] for e in entries if (indexed.get(e["key"]) or {}).get("hash") != e["hash"]]
    print(f"Index : {len(indexed)} resource(s) — model {index.get('model') or '(none)'} / "
          f"{index.get('dim') or 0} dimensions")
    print(f"Atlas : {len(entries)} resource(s) — {len(stale)} not indexed")
    return 0
