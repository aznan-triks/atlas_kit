"""Semantic mode: build a vector index over the mechanical atlas, search it by meaning.

Provider-agnostic — this module never talks HTTP directly, it goes through
`providers.EmbeddingProvider`. Same incremental-hash doctrine as `scan.py`.
"""
from __future__ import annotations

import hashlib
import os
import statistics
import sys
from pathlib import Path

from atlas_kit.index_store import load_json, save_json
from atlas_kit.providers import get_provider
from atlas_kit.providers.base import EmbedRequest, EmbeddingError, InvalidApiKey, QuotaExhausted, l2_normalize

DEFAULT_BATCH_SIZE = 50
DEFAULT_TOP_K = 8
# Historical: the old naive fixed-cosine cutoff. No longer used by search() — kept
# only because tests/test_semantic_centroid_bug.py pins it to document the bug it
# used to cause (shared-domain bias inflates cosine well above this).
DEFAULT_MIN_SCORE = 0.55
# --min-score is now a z-score multiplier k, not an absolute cosine cutoff: a
# candidate is kept only if score >= mean + k*stdev of the full score distribution
# (see search()). This is a breaking change of the --min-score CLI flag's MEANING;
# the flag name itself is unchanged.
DEFAULT_MIN_ZSCORE = 1.0
# `similar` has its own, stricter default — near-duplicate detection wants precision
# over recall, so it does not reuse DEFAULT_MIN_ZSCORE.
DEFAULT_SIMILAR_MIN_ZSCORE = 2.0
DEFAULT_TIMEOUT_S = 60.0
CURRENT_KEY_SCHEMA = 2
MIN_ENTRIES_FOR_ZSCORE = 5


def entry_key(section: str, row: dict) -> str:
    return f"{section}::{row.get('name', '')}::{row.get('file', '')}::{row.get('line', 0)}"


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


def centroid(vectors: list[list[float]]) -> list[float]:
    """Mean of `vectors`, NOT renormalized. Global mean only (no per-section split,
    no PCA/SVD — per-section starves small sections at this corpus size, PCA/SVD is
    unstable at low N)."""
    if not vectors:
        return []
    dim = len(vectors[0])
    sums = [0.0] * dim
    for vec in vectors:
        for i, x in enumerate(vec):
            sums[i] += x
    n = len(vectors)
    return [s / n for s in sums]


def recentre(vector: list[float], centroid_vector: list[float]) -> list[float]:
    """Subtract the corpus centroid, then L2-normalize — collapses the shared-domain
    bias that inflates cosine similarity between otherwise-unrelated entries."""
    return l2_normalize([x - c for x, c in zip(vector, centroid_vector)])


def recentred_entries(index: dict) -> list[dict]:
    """Every entry in `index`, with its vector recentred against `index['centroid']`
    (under key "vector_r"). Pure, I/O-free — the shared recentring step behind both
    score_entries() (query vs. corpus) and similar_pairs() (corpus vs. itself), so
    the recentring math is written once.

    Raises ValueError if the index has no centroid (predates this feature / needs a
    fresh `embed` run) — never silently falls back to raw un-recentred cosine.
    """
    idx_centroid = index.get("centroid")
    if not idx_centroid:
        raise ValueError("Index predates centroid-based search — run `atlas-kit embed` to rebuild.")
    out = []
    for key, row in (index.get("entries") or {}).items():
        out.append({**row, "key": key, "vector_r": recentre(row.get("vector") or [], idx_centroid)})
    return out


def score_entries(index: dict, query_vector: list[float]) -> list[dict]:
    """Score every entry in `index` against `query_vector`, both recentred against
    `index['centroid']`. Pure, I/O-free — shared by search().

    Raises ValueError if the index has no centroid (predates this feature / needs a
    fresh `embed` run) — never silently falls back to raw un-recentred cosine.
    """
    entries_r = recentred_entries(index)  # raises ValueError if no centroid
    query_r = recentre(query_vector, index["centroid"])
    return [{**e, "score": cosine(query_r, e["vector_r"])} for e in entries_r]


def similar_pairs(index: dict, min_score: float) -> list[dict]:
    """All-pairs cosine similarity across index['entries'], each vector recentred
    against index['centroid'] via recentred_entries() (same recentring helper
    score_entries()/search() use — no second implementation of the recentring math).

    Applies the same z-score gate discipline as search(): with fewer than
    MIN_ENTRIES_FOR_ZSCORE *pairs*, the gate is skipped (unstable at low N) and every
    pair is kept. Otherwise a pair is kept only if score >= mean + k*stdev of the
    full pair-score population.

    Returns pairs sorted by score descending. Each pair is {"a": entry, "b": entry,
    "score": float}. Raises ValueError if the index has no centroid.
    """
    entries_r = recentred_entries(index)
    pairs = []
    for i in range(len(entries_r)):
        for j in range(i + 1, len(entries_r)):
            a, b = entries_r[i], entries_r[j]
            pairs.append({"a": a, "b": b, "score": cosine(a["vector_r"], b["vector_r"])})

    if len(pairs) < MIN_ENTRIES_FOR_ZSCORE:
        hits = pairs
    else:
        scores = [p["score"] for p in pairs]
        mean = statistics.mean(scores)
        stdev = statistics.pstdev(scores)
        cutoff = mean + min_score * stdev
        hits = [p for p in pairs if p["score"] >= cutoff]

    hits.sort(key=lambda p: p["score"], reverse=True)
    return hits


def search(index: dict, query_vector: list[float], top_k: int, min_score: float,
          section: str | None = None) -> list[dict]:
    """`min_score` is a z-score multiplier k (see DEFAULT_MIN_ZSCORE), NOT an
    absolute cosine cutoff: a candidate is kept only if
    score >= mean + k*stdev of the FULL (global, unfiltered) score distribution.

    With fewer than MIN_ENTRIES_FOR_ZSCORE entries in the index, the z-score gate
    is skipped entirely (unstable at low N) and every scored candidate is kept,
    capped only by top_k.

    `section` is applied AFTER the z-score gate, as a pure post-filter — this keeps
    the z-score population independent of `section`, avoiding small-n instability.

    Raises ValueError (via score_entries) if the index has no centroid.
    """
    scored = score_entries(index, query_vector)

    if len(scored) < MIN_ENTRIES_FOR_ZSCORE:
        hits = scored
    else:
        scores = [h["score"] for h in scored]
        mean = statistics.mean(scores)
        stdev = statistics.pstdev(scores)
        cutoff = mean + min_score * stdev
        hits = [h for h in scored if h["score"] >= cutoff]

    if section:
        hits = [h for h in hits if h.get("section") == section]

    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:top_k]


def _resolve_api_keys(provider) -> list[str]:
    """Reads `{ENV_VAR}S` (plural, comma-separated) first — enables key rotation on
    quota exhaustion. Falls back to the singular `{ENV_VAR}` as a single-key list.
    Returns [] if neither is set."""
    plural = os.environ.get(f"{provider.env_var}S")
    if plural:
        keys = [k.strip() for k in plural.split(",") if k.strip()]
        if keys:
            return keys
    single = os.environ.get(provider.env_var)
    return [single] if single else []


def _embed_with_rotation(provider, keys: list[str], request: EmbedRequest) -> list[list[float]]:
    """Tries each key in order, rotating to the next ONLY on QuotaExhausted (HTTP 429).
    InvalidApiKey is never rotated past — Fail Fast, propagates immediately (a bad key
    is a config error, not a capacity problem another key would fix). Every rotation is
    printed (key identified by position, never by value) — never a silent switch."""
    last_exc: QuotaExhausted | None = None
    for i, key in enumerate(keys, start=1):
        request.api_key = key
        try:
            return provider.embed(request)
        except QuotaExhausted as exc:
            last_exc = exc
            if i < len(keys):
                print(f"Key {i}/{len(keys)} exhausted (HTTP 429) — rotating to key {i + 1}/{len(keys)}.",
                      file=sys.stderr)
            continue
    raise last_exc


def cmd_embed(atlas_path: Path, index_path: Path, provider_name: str, model: str | None,
             dimensions: int | None, batch_size: int, timeout_s: float) -> int:
    provider = get_provider(provider_name)
    model = model or provider.default_model
    dim = dimensions or provider.default_dimensions

    if provider.requires_api_key:
        api_keys = _resolve_api_keys(provider)
        if not api_keys:
            print(f"Missing API key: set {provider.env_var} (or {provider.env_var}S for "
                  f"multiple, comma-separated, rotated on quota) to use --provider {provider.name}.",
                  file=sys.stderr)
            return 2
    else:
        api_keys = [""]

    atlas = load_json(atlas_path, {"symbols": {}})
    entries = iter_atlas_entries(atlas, model=model, dim=dim)
    index = load_json(index_path, {"model": "", "dim": 0, "entries": {}})

    if index.get("model") != model or int(index.get("dim") or 0) != dim:
        index = {"model": model, "dim": dim, "entries": {}, "key_schema": CURRENT_KEY_SCHEMA}

    # 1. Key migration — local, deterministic, lossless rekey; zero API calls. Every
    # existing entry already carries section/name/file/line as fields independent of
    # its dict key, so we can always recompute the current-format key from the value.
    if index.get("entries") and index.get("key_schema") != CURRENT_KEY_SCHEMA:
        migrated = {entry_key(value.get("section", ""), value): value
                   for value in index["entries"].values()}
        print(f"Migrated {len(migrated)} index key(s) to schema {CURRENT_KEY_SCHEMA}.")
        index["entries"] = migrated
    index["key_schema"] = CURRENT_KEY_SCHEMA

    # 2. Prune index entries with no matching atlas entry anymore (renamed/removed
    # symbols, or the pre-fix collision bug's already-lost victims).
    current_keys = {e["key"] for e in entries}
    stale_keys = [k for k in index["entries"] if k not in current_keys]
    for k in stale_keys:
        del index["entries"][k]
    if stale_keys:
        print(f"Pruned {len(stale_keys)} stale index entrie(s).")

    def _recompute_centroid() -> None:
        vectors = [row["vector"] for row in index["entries"].values() if row.get("vector")]
        index["centroid"] = centroid(vectors)

    # 3. Embed pending entries (existing incremental-hash logic).
    todo = pending_entries(entries, index, model=model, dim=dim)
    if not todo:
        print(f"0 new entries — index up to date ({len(entries)} resources).")
        _recompute_centroid()
        save_json(index_path, index)
        return 0

    print(f"{len(todo)} entrie(s) to index out of {len(entries)} (batches of {batch_size})...")
    done = 0
    try:
        for start in range(0, len(todo), batch_size):
            chunk = todo[start:start + batch_size]
            request = EmbedRequest(texts=[e["text"] for e in chunk], task_type="document",
                                   model=model, dimensions=dim, api_key=api_keys[0], timeout_s=timeout_s)
            vectors = _embed_with_rotation(provider, api_keys, request)
            for entry, vector in zip(chunk, vectors):
                index["entries"][entry["key"]] = {
                    "section": entry["section"], "name": entry["name"], "file": entry["file"],
                    "line": entry["line"], "signature": entry["signature"],
                    "docstring": entry["docstring"], "hash": entry["hash"], "vector": vector,
                }
            done += len(chunk)
            print(f"  {done}/{len(todo)}")
    except (QuotaExhausted, InvalidApiKey, EmbeddingError) as exc:
        # 4. Recompute the centroid over ALL current entries' vectors — every write.
        _recompute_centroid()
        save_json(index_path, index)
        print(f"\n{exc}", file=sys.stderr)
        return 2

    # 4. Recompute the centroid over ALL current entries' vectors — every write.
    _recompute_centroid()
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
    if not index.get("centroid"):
        print("Index predates centroid-based search — run `atlas-kit embed` to rebuild.",
              file=sys.stderr)
        return 2

    provider = get_provider(provider_name)
    model = model or provider.default_model
    dim = dimensions or provider.default_dimensions

    if provider.requires_api_key:
        api_keys = _resolve_api_keys(provider)
        if not api_keys:
            print(f"Missing API key: set {provider.env_var} (or {provider.env_var}S for "
                  f"multiple, comma-separated, rotated on quota) to use --provider {provider.name}.",
                  file=sys.stderr)
            return 2
    else:
        api_keys = [""]

    request = EmbedRequest(texts=[question], task_type="query", model=model, dimensions=dim,
                           api_key=api_keys[0], timeout_s=timeout_s)
    try:
        query_vector = _embed_with_rotation(provider, api_keys, request)[0]
    except (QuotaExhausted, InvalidApiKey, EmbeddingError) as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2

    n_entries = len(index.get("entries") or {})
    if n_entries < MIN_ENTRIES_FOR_ZSCORE:
        print(f"{n_entries}<{MIN_ENTRIES_FOR_ZSCORE} candidates — relative threshold disabled, "
              f"returning top_k only.")

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


def cmd_similar(index_path: Path, min_score: float, section: str | None,
                exclude_same_file: bool) -> int:
    """Offline near-duplicate report: all-pairs cosine over the stored index, no
    network call, no API key. Same fail-fast discipline as cmd_search — a
    centroid-less index is a hard error, never a silent raw-cosine fallback.

    `section` is applied AFTER the z-score gate, as a pure post-filter restricting
    which pairs are shown to those entirely within that section — mirrors search()'s
    decision to keep the z-score population independent of `section` (full index,
    not a section subset), for the same reason: avoiding small-n instability in the
    gate itself. "Only compare within one section" (the flag's intent) is honoured at
    display time, not by shrinking the scored population.

    `exclude_same_file` is also a post-gate display filter, never silent: the header
    always states how many same-file pairs were included or excluded.
    """
    index = load_json(index_path, {"model": "", "dim": 0, "entries": {}})
    if not index.get("entries"):
        print("Index is empty — run `atlas-kit embed` first.", file=sys.stderr)
        return 1
    if not index.get("centroid"):
        print("Index predates centroid-based search — run `atlas-kit embed` to rebuild.",
              file=sys.stderr)
        return 2

    n_entries = len(index["entries"])
    n_pairs = n_entries * (n_entries - 1) // 2
    if n_pairs < MIN_ENTRIES_FOR_ZSCORE:
        print(f"{n_pairs}<{MIN_ENTRIES_FOR_ZSCORE} pairs — relative threshold disabled, "
              f"returning all pairs.")

    pairs = similar_pairs(index, min_score)

    same_file = [p for p in pairs if p["a"].get("file") == p["b"].get("file")]
    if exclude_same_file:
        pairs = [p for p in pairs if p["a"].get("file") != p["b"].get("file")]
        print(f"Compared {n_entries} indexed entries ({n_pairs} pair(s) considered) — "
              f"excluded {len(same_file)} same-file pair(s) (--exclude-same-file).")
    else:
        print(f"Compared {n_entries} indexed entries ({n_pairs} pair(s) considered) — "
              f"included {len(same_file)} same-file pair(s) (default; pass "
              f"--exclude-same-file to drop them).")

    if section:
        pairs = [p for p in pairs if p["a"].get("section") == section and p["b"].get("section") == section]

    if not pairs:
        print("No pairs above threshold.")
        return 0

    for p in pairs:
        a, b = p["a"], p["b"]
        print(f"  [{p['score']:.2f}] {a['section']}::{a['name']}  {a['file']}:{a['line']}"
              f"   <->   {b['section']}::{b['name']}  {b['file']}:{b['line']}")
    print(f"\n{len(pairs)} pair(s).")
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
