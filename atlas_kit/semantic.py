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

from atlas_kit import emit
from atlas_kit.index_store import atlas_schema_error, load_json, save_json
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


def index_schema_error(index: dict, path: Path) -> str | None:
    """Return an actionable message if `index` was written under a key schema this
    code cannot read correctly, else None. Mirrors `index_store.atlas_schema_error`,
    for the semantic index instead of the atlas.

    Written ONCE and called by every index READER (`search`, `similar`, `status`) —
    they used to trust `index['entries']` blindly, so an index written under the old
    key schema was read as if it were current and every key silently mismatched.
    `cmd_embed` deliberately does NOT call this: it is the one command that can fix
    the file (it migrates the keys in place, see cmd_embed step 1), which is exactly
    why every message here says "run `atlas-kit embed`".

    An index with no entries carries no keys to misread, so it gets no verdict — a
    missing/empty index must stay reportable by `status` and must keep producing
    `search`/`similar`'s own "index is empty" message, not a schema error.

    An absent `key_schema` field means schema 1 (keys were section::name::file, with
    no line number): readable as JSON, but the keys mean something else, so it is
    refused rather than half-trusted.
    """
    if not index.get("entries"):
        return None
    found = int(index.get("key_schema") or 1)
    if found == CURRENT_KEY_SCHEMA:
        return None
    if found > CURRENT_KEY_SCHEMA:
        return (f"Index {path} was written by a newer atlas-kit (key schema {found} > "
                f"{CURRENT_KEY_SCHEMA}) — upgrade atlas-kit, or re-run `atlas-kit embed`.")
    return (f"Index {path} uses key schema {found}, this atlas-kit expects "
            f"{CURRENT_KEY_SCHEMA} — re-run `atlas-kit embed` to rebuild it.")


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


def _embed_with_rotation(provider, keys: list[str],
                         request: EmbedRequest) -> tuple[list[list[float]], list[str]]:
    """Tries each key in order, rotating to the next ONLY on QuotaExhausted (HTTP 429).
    InvalidApiKey is never rotated past — Fail Fast, propagates immediately (a bad key
    is a config error, not a capacity problem another key would fix). Every rotation is
    printed (key identified by position, never by value) — never a silent switch.

    Returns (vectors, keys_from_working_key_onward): callers doing multiple calls
    (e.g. one per batch) must reuse the returned key list for the next call, so an
    already-exhausted key is dropped for good instead of being retried every batch."""
    last_exc: QuotaExhausted | None = None
    for i, key in enumerate(keys, start=1):
        request.api_key = key
        try:
            return provider.embed(request), keys[i - 1:]
        except QuotaExhausted as exc:
            last_exc = exc
            if i < len(keys):
                print(f"Key {i}/{len(keys)} exhausted (HTTP 429) — rotating to key {i + 1}/{len(keys)}.",
                      file=sys.stderr)
            continue
    raise last_exc


def cmd_embed(atlas_path: Path, index_path: Path, provider_name: str, model: str | None,
             dimensions: int | None, batch_size: int, timeout_s: float,
             as_json: bool = False) -> int:
    if not atlas_path.exists():
        emit.fail("embed", f"Atlas not found: {atlas_path} — run `atlas-kit scan` first, or pass "
                  f"--atlas <path>. Refusing to treat a missing atlas as empty (that would "
                  f"prune the entire index as stale).", as_json)
        return 2

    provider = get_provider(provider_name)
    model = model or provider.default_model
    dim = dimensions or provider.default_dimensions

    if provider.requires_api_key:
        api_keys = _resolve_api_keys(provider)
        if not api_keys:
            emit.fail("embed", f"Missing API key: set {provider.env_var} (or {provider.env_var}S "
                      f"for multiple, comma-separated, rotated on quota) to use --provider "
                      f"{provider.name}.", as_json)
            return 2
    else:
        api_keys = [""]

    atlas = load_json(atlas_path, {"symbols": {}})
    # Same reasoning as the missing-atlas guard above, one step further: an atlas this
    # code cannot read correctly must never be treated as authoritative, because the
    # prune step below deletes every index entry it cannot find in it.
    schema_error = atlas_schema_error(atlas, atlas_path)
    if schema_error:
        emit.fail("embed", schema_error, as_json)
        return 2

    entries = iter_atlas_entries(atlas, model=model, dim=dim)
    index = load_json(index_path, {"model": "", "dim": 0, "entries": {}})

    if index.get("model") != model or int(index.get("dim") or 0) != dim:
        if index.get("entries"):
            print(f"Index model/dim changed ({index.get('model')!r}/{index.get('dim')!r} -> "
                  f"{model!r}/{dim}) — starting a fresh index, {len(index['entries'])} old "
                  f"vector(s) discarded (incompatible dimensions).", file=sys.stderr)
        index = {"model": model, "dim": dim, "entries": {}, "key_schema": CURRENT_KEY_SCHEMA}

    # 1. Key migration — local, deterministic, lossless rekey; zero API calls. Every
    # existing entry already carries section/name/file/line as fields independent of
    # its dict key, so we can always recompute the current-format key from the value.
    migrated_count = 0
    if index.get("entries") and index.get("key_schema") != CURRENT_KEY_SCHEMA:
        migrated = {entry_key(value.get("section", ""), value): value
                   for value in index["entries"].values()}
        migrated_count = len(migrated)
        if not as_json:
            print(f"Migrated {len(migrated)} index key(s) to schema {CURRENT_KEY_SCHEMA}.")
        index["entries"] = migrated
    index["key_schema"] = CURRENT_KEY_SCHEMA

    # 2. Prune index entries with no matching atlas entry anymore (renamed/removed
    # symbols, or the pre-fix collision bug's already-lost victims).
    current_keys = {e["key"] for e in entries}
    stale_keys = [k for k in index["entries"] if k not in current_keys]
    for k in stale_keys:
        del index["entries"][k]
    if stale_keys and not as_json:
        print(f"Pruned {len(stale_keys)} stale index entrie(s).")

    def _recompute_centroid() -> None:
        vectors = [row["vector"] for row in index["entries"].values() if row.get("vector")]
        index["centroid"] = centroid(vectors)

    def _report(human_line: str) -> None:
        """The single success exit shape — one JSON envelope, or the human line.
        Both `embed` success paths (nothing to do / index written) go through here so
        the two modes can never drift apart."""
        if as_json:
            emit.json_ok("embed", atlas=str(atlas_path), index=str(index_path),
                         provider=provider.name, model=model, dim=dim,
                         entries_total=len(entries), entries_indexed=len(index["entries"]),
                         pruned=len(stale_keys), migrated=migrated_count)
        else:
            print(human_line)

    # 3. Embed pending entries (existing incremental-hash logic).
    todo = pending_entries(entries, index, model=model, dim=dim)
    if not todo:
        _recompute_centroid()
        save_json(index_path, index)
        _report(f"0 new entries — index up to date ({len(entries)} resources).")
        return 0

    if not as_json:
        print(f"{len(todo)} entrie(s) to index out of {len(entries)} (batches of {batch_size})...")
    done = 0
    try:
        for start in range(0, len(todo), batch_size):
            chunk = todo[start:start + batch_size]
            request = EmbedRequest(texts=[e["text"] for e in chunk], task_type="document",
                                   model=model, dimensions=dim, api_key=api_keys[0], timeout_s=timeout_s)
            vectors, api_keys = _embed_with_rotation(provider, api_keys, request)
            for entry, vector in zip(chunk, vectors):
                index["entries"][entry["key"]] = {
                    "section": entry["section"], "name": entry["name"], "file": entry["file"],
                    "line": entry["line"], "signature": entry["signature"],
                    "docstring": entry["docstring"], "hash": entry["hash"], "vector": vector,
                }
            done += len(chunk)
            # Progress belongs to the human stream only — in JSON mode it would break
            # the "exactly one object on stdout" contract emit.py owns.
            if not as_json:
                print(f"  {done}/{len(todo)}")
    except (QuotaExhausted, InvalidApiKey, EmbeddingError) as exc:
        # 4. Recompute the centroid over ALL current entries' vectors — every write.
        _recompute_centroid()
        save_json(index_path, index)
        # The blank line separates the error from the progress lines above it; JSON
        # mode printed none, and a leading newline inside a JSON string is noise.
        if not as_json:
            print("", file=sys.stderr)
        emit.fail("embed", str(exc), as_json)
        return 2

    # 4. Recompute the centroid over ALL current entries' vectors — every write.
    _recompute_centroid()
    save_json(index_path, index)
    _report(f"Index written: {index_path} ({len(index['entries'])} resources).")
    return 0


def cmd_search(question: str, index_path: Path, provider_name: str,
               model: str | None, dimensions: int | None, top_k: int, min_score: float,
               section: str | None, timeout_s: float, as_json: bool = False) -> int:
    index = load_json(index_path, {"model": "", "dim": 0, "entries": {}})
    if not index.get("entries"):
        emit.fail("search", "Index is empty — run `atlas-kit embed` first.", as_json)
        return 1
    schema_error = index_schema_error(index, index_path)
    if schema_error:
        emit.fail("search", schema_error, as_json)
        return 2
    if not index.get("centroid"):
        emit.fail("search", "Index predates centroid-based search — run `atlas-kit embed` "
                  "to rebuild.", as_json)
        return 2

    provider = get_provider(provider_name)
    model = model or provider.default_model
    dim = dimensions or provider.default_dimensions

    if provider.requires_api_key:
        api_keys = _resolve_api_keys(provider)
        if not api_keys:
            emit.fail("search", f"Missing API key: set {provider.env_var} (or "
                      f"{provider.env_var}S for multiple, comma-separated, rotated on quota) "
                      f"to use --provider {provider.name}.", as_json)
            return 2
    else:
        api_keys = [""]

    request = EmbedRequest(texts=[question], task_type="query", model=model, dimensions=dim,
                           api_key=api_keys[0], timeout_s=timeout_s)
    try:
        query_vector = _embed_with_rotation(provider, api_keys, request)[0][0]
    except (QuotaExhausted, InvalidApiKey, EmbeddingError) as exc:
        if not as_json:
            print("", file=sys.stderr)
        emit.fail("search", str(exc), as_json)
        return 2

    n_entries = len(index.get("entries") or {})
    # Whether the z-score gate ran at all is a result, not a log line: humans get the
    # sentence, machines get `threshold_applied` in the payload — same fact, one source.
    threshold_applied = n_entries >= MIN_ENTRIES_FOR_ZSCORE
    if not threshold_applied and not as_json:
        print(f"{n_entries}<{MIN_ENTRIES_FOR_ZSCORE} candidates — relative threshold disabled, "
              f"returning top_k only.")

    hits = search(index, query_vector, top_k=top_k, min_score=min_score, section=section)

    if as_json:
        emit.json_ok("search", question=question, count=len(hits),
                     threshold_applied=threshold_applied,
                     results=[{"score": hit["score"], "section": hit.get("section", ""),
                               "name": hit.get("name", ""), "file": hit.get("file", ""),
                               "line": hit.get("line", 0),
                               "signature": hit.get("signature", ""),
                               "docstring": hit.get("docstring") or ""} for hit in hits])
        return 0

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


def _pair_side(entry: dict) -> dict:
    """The identity fields of one side of a `similar` pair — deliberately NOT the whole
    row: an index entry also carries `vector` (and the recentred `vector_r`), which would
    bloat the JSON payload by three orders of magnitude and tell a consumer nothing."""
    return {"section": entry.get("section", ""), "name": entry.get("name", ""),
            "file": entry.get("file", ""), "line": entry.get("line", 0)}


def cmd_similar(index_path: Path, min_score: float, section: str | None,
                exclude_same_file: bool, as_json: bool = False) -> int:
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
        emit.fail("similar", "Index is empty — run `atlas-kit embed` first.", as_json)
        return 1
    schema_error = index_schema_error(index, index_path)
    if schema_error:
        emit.fail("similar", schema_error, as_json)
        return 2
    if not index.get("centroid"):
        emit.fail("similar", "Index predates centroid-based search — run `atlas-kit embed` "
                  "to rebuild.", as_json)
        return 2

    n_entries = len(index["entries"])
    n_pairs = n_entries * (n_entries - 1) // 2
    if n_pairs < MIN_ENTRIES_FOR_ZSCORE and not as_json:
        print(f"{n_pairs}<{MIN_ENTRIES_FOR_ZSCORE} pairs — relative threshold disabled, "
              f"returning all pairs.")

    pairs = similar_pairs(index, min_score)

    same_file = [p for p in pairs if p["a"].get("file") == p["b"].get("file")]
    if exclude_same_file:
        pairs = [p for p in pairs if p["a"].get("file") != p["b"].get("file")]
        if not as_json:
            print(f"Compared {n_entries} indexed entries ({n_pairs} pair(s) considered) — "
                  f"excluded {len(same_file)} same-file pair(s) (--exclude-same-file).")
    elif not as_json:
        print(f"Compared {n_entries} indexed entries ({n_pairs} pair(s) considered) — "
              f"included {len(same_file)} same-file pair(s) (default; pass "
              f"--exclude-same-file to drop them).")

    if section:
        pairs = [p for p in pairs if p["a"].get("section") == section and p["b"].get("section") == section]

    if as_json:
        # The header's counts are payload keys here — same numbers, machine-readable:
        # `excluded_same_file` says which way the flag went, `same_file_pairs` how many
        # pairs it concerned. Never silent, in either mode.
        emit.json_ok("similar", count=len(pairs), entries=n_entries, pairs_considered=n_pairs,
                     same_file_pairs=len(same_file), excluded_same_file=exclude_same_file,
                     pairs=[{"score": p["score"], "a": _pair_side(p["a"]), "b": _pair_side(p["b"])}
                            for p in pairs])
        return 0

    if not pairs:
        print("No pairs above threshold.")
        return 0

    for p in pairs:
        a, b = p["a"], p["b"]
        print(f"  [{p['score']:.2f}] {a['section']}::{a['name']}  {a['file']}:{a['line']}"
              f"   <->   {b['section']}::{b['name']}  {b['file']}:{b['line']}")
    print(f"\n{len(pairs)} pair(s).")
    return 0


def cmd_status(atlas_path: Path, index_path: Path, as_json: bool = False) -> int:
    if not atlas_path.exists():
        emit.fail("status", f"Atlas not found: {atlas_path} — run `atlas-kit scan` first, or "
                  f"pass --atlas <path>.", as_json)
        return 2

    index = load_json(index_path, {"model": "", "dim": 0, "entries": {}})
    schema_error = index_schema_error(index, index_path)
    if schema_error:
        emit.fail("status", schema_error, as_json)
        return 2

    atlas = load_json(atlas_path, {"symbols": {}})
    # An atlas this code cannot read correctly cannot be compared against the index
    # either — the "not indexed" count below would be fiction. Refuse before comparing.
    atlas_error = atlas_schema_error(atlas, atlas_path)
    if atlas_error:
        emit.fail("status", atlas_error, as_json)
        return 2

    entries = iter_atlas_entries(atlas, model=index.get("model", ""), dim=int(index.get("dim") or 0))
    indexed = index.get("entries") or {}
    stale = [e["key"] for e in entries if (indexed.get(e["key"]) or {}).get("hash") != e["hash"]]

    if as_json:
        # key_schema 0 means "field absent", which only reaches here on an empty index —
        # a non-empty one without the field is schema 1 and was refused above.
        emit.json_ok("status",
                     atlas={"path": str(atlas_path), "resources": len(entries),
                            "schema_version": int(atlas.get("schema_version") or 0)},
                     index={"path": str(index_path), "resources": len(indexed),
                            "model": index.get("model") or "", "dim": int(index.get("dim") or 0),
                            "key_schema": int(index.get("key_schema") or 0),
                            "stale": len(stale)})
        return 0

    print(f"Index : {len(indexed)} resource(s) — model {index.get('model') or '(none)'} / "
          f"{index.get('dim') or 0} dimensions")
    print(f"Atlas : {len(entries)} resource(s) — {len(stale)} not indexed")
    return 0
