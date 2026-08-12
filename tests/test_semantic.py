"""Tests — atlas_kit.semantic (index build + cosine search). No network."""
from __future__ import annotations

from atlas_kit.semantic import cosine, entry_hash, iter_atlas_entries, pending_entries, search


def _atlas():
    return {"symbols": {
        "python_functions": [
            {"name": "cancel_job", "file": "jobs.py", "line": 10,
             "signature": "def cancel_job(job_id)", "docstring": "Cancel a running job."},
        ],
    }}


def test_iter_atlas_entries_produces_stable_keys_and_hashes():
    entries = iter_atlas_entries(_atlas(), model="m", dim=4)
    assert len(entries) == 1
    assert entries[0]["key"] == "python_functions::cancel_job::jobs.py"
    assert entries[0]["hash"] == entry_hash(entries[0]["text"], "m", 4)


def test_pending_entries_skips_unchanged_hash():
    entries = iter_atlas_entries(_atlas(), model="m", dim=4)
    index = {"model": "m", "dim": 4, "entries": {entries[0]["key"]: {"hash": entries[0]["hash"]}}}
    assert pending_entries(entries, index, model="m", dim=4) == []


def test_pending_entries_full_rebuild_when_model_changes():
    entries = iter_atlas_entries(_atlas(), model="m", dim=4)
    index = {"model": "other-model", "dim": 4, "entries": {}}
    assert pending_entries(entries, index, model="m", dim=4) == entries


def test_cosine_of_identical_unit_vectors_is_one():
    assert abs(cosine([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-9


def test_search_filters_by_min_score_and_section():
    index = {"entries": {
        "a": {"section": "python_functions", "name": "cancel_job", "vector": [1.0, 0.0]},
        "b": {"section": "python_functions", "name": "unrelated", "vector": [0.0, 1.0]},
    }}
    hits = search(index, query_vector=[1.0, 0.0], top_k=5, min_score=0.5)
    assert [h["name"] for h in hits] == ["cancel_job"]

    hits_wrong_section = search(index, query_vector=[1.0, 0.0], top_k=5, min_score=0.5,
                                section="python_classes")
    assert hits_wrong_section == []
