"""Tests — code_fauna_codex.semantic (index build + cosine search). No network."""
from __future__ import annotations

from code_fauna_codex.providers.base import l2_normalize
from code_fauna_codex.semantic import (
    DEFAULT_MIN_SCORE, centroid, cosine, entry_hash, iter_codex_entries, pending_entries,
    recentre, search,
)


def _codex():
    return {"symbols": {
        "python_functions": [
            {"name": "cancel_job", "file": "jobs.py", "line": 10,
             "signature": "def cancel_job(job_id)", "docstring": "Cancel a running job."},
        ],
    }}


def test_iter_codex_entries_produces_stable_keys_and_hashes():
    entries = iter_codex_entries(_codex(), model="m", dim=4)
    assert len(entries) == 1
    # Key includes the line number (fix for the duplicate-name collision bug — see
    # tests/test_semantic_dedup_bug.py).
    assert entries[0]["key"] == "python_functions::cancel_job::jobs.py::10"
    assert entries[0]["hash"] == entry_hash(entries[0]["text"], "m", 4)


def test_pending_entries_skips_unchanged_hash():
    entries = iter_codex_entries(_codex(), model="m", dim=4)
    index = {"model": "m", "dim": 4, "entries": {entries[0]["key"]: {"hash": entries[0]["hash"]}}}
    assert pending_entries(entries, index, model="m", dim=4) == []


def test_pending_entries_full_rebuild_when_model_changes():
    entries = iter_codex_entries(_codex(), model="m", dim=4)
    index = {"model": "other-model", "dim": 4, "entries": {}}
    assert pending_entries(entries, index, model="m", dim=4) == entries


def test_cosine_of_identical_unit_vectors_is_one():
    assert abs(cosine([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-9


def test_search_below_min_entries_skips_zscore_gate():
    # min_score is now a z-score multiplier (see semantic.search docstring). With
    # fewer than 5 entries the gate is skipped entirely — every candidate survives,
    # capped only by top_k.
    index = {"centroid": [0.0, 0.0], "entries": {
        "a": {"section": "python_functions", "name": "cancel_job", "vector": [1.0, 0.0]},
        "b": {"section": "python_functions", "name": "unrelated", "vector": [0.0, 1.0]},
    }}
    hits = search(index, query_vector=[1.0, 0.0], top_k=5, min_score=1.0)
    assert {h["name"] for h in hits} == {"cancel_job", "unrelated"}


def test_search_applies_section_filter_after_zscore_gate():
    # 5 entries: enough for the z-score gate to activate. Two candidates
    # ("cancel_job", "close_cousin") clear the mean+k*stdev cutoff; three others are
    # far off and get gated out. --section is then applied to the survivors only.
    index = {"centroid": [0.0, 0.0], "entries": {
        "a": {"section": "python_functions", "name": "cancel_job", "vector": [1.0, 0.0]},
        "b": {"section": "python_classes", "name": "close_cousin", "vector": [1.0, 0.5]},
        "c": {"section": "python_functions", "name": "far1", "vector": [-1.0, 0.0]},
        "d": {"section": "python_functions", "name": "far2", "vector": [-1.0, -0.5]},
        "e": {"section": "python_functions", "name": "far3", "vector": [0.0, -1.0]},
    }}
    hits = search(index, query_vector=[1.0, 0.0], top_k=5, min_score=1.0)
    assert [h["name"] for h in hits] == ["cancel_job", "close_cousin"]

    hits_filtered = search(index, query_vector=[1.0, 0.0], top_k=5, min_score=1.0,
                           section="python_functions")
    assert [h["name"] for h in hits_filtered] == ["cancel_job"]


def test_search_recentres_query_same_way_as_stored_vectors():
    """Reuses the shared-domain-bias construction from
    tests/test_semantic_centroid_bug.py: N unit vectors sharing a fixed bias b
    (weight 0.85) plus a small, mutually-orthogonal per-i direction (weight 0.15).

    A query built the same way as the corpus (using entry 0's own direction) barely
    discriminates entry 0 from an unrelated entry under raw cosine — both score well
    above the historical fixed threshold (DEFAULT_MIN_SCORE), with only a small gap.
    Once search() recentres the query against index['centroid'] the same way stored
    vectors are recentred, entry 0 stands out clearly and the z-score gate filters
    the unrelated entries out entirely.
    """
    dim, n, bias_index = 16, 12, 15
    bias_weight, direction_weight = 0.85, 0.15

    def one_hot(i):
        v = [0.0] * dim
        v[i] = 1.0
        return v

    def orthogonal_component(v, b):
        dot_vb = sum(x * y for x, y in zip(v, b))
        return [x - dot_vb * y for x, y in zip(v, b)]

    b = one_hot(bias_index)
    directions = [l2_normalize(orthogonal_component(one_hot(i), b)) for i in range(n)]
    vectors = [l2_normalize([bias_weight * b[k] + direction_weight * directions[i][k]
                             for k in range(dim)]) for i in range(n)]

    query = vectors[0]  # built exactly like the corpus, same direction as vectors[0]

    raw_target = cosine(query, vectors[0])
    raw_unrelated = cosine(query, vectors[1])
    assert raw_target > DEFAULT_MIN_SCORE and raw_unrelated > DEFAULT_MIN_SCORE
    assert raw_target - raw_unrelated < 0.1, "raw cosine barely discriminates target from unrelated"

    idx_centroid = centroid(vectors)
    recentred_target = cosine(recentre(query, idx_centroid), recentre(vectors[0], idx_centroid))
    recentred_unrelated = cosine(recentre(query, idx_centroid), recentre(vectors[1], idx_centroid))
    assert recentred_target > 0.9
    assert recentred_unrelated < 0.1
    assert recentred_target - recentred_unrelated > 0.8, "recentring cleanly discriminates them"

    index = {"centroid": idx_centroid, "entries": {
        f"e{i}": {"section": "s", "name": f"n{i}", "vector": vectors[i]} for i in range(n)
    }}
    hits = search(index, query_vector=query, top_k=n, min_score=1.0)
    assert hits[0]["name"] == "n0"


def test_search_raises_when_index_has_no_centroid():
    index = {"entries": {
        "a": {"section": "python_functions", "name": "cancel_job", "vector": [1.0, 0.0]},
    }}
    try:
        search(index, query_vector=[1.0, 0.0], top_k=5, min_score=1.0)
        assert False, "expected ValueError for a centroid-less (unmigrated) index"
    except ValueError:
        pass
