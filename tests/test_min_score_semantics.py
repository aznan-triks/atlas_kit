"""Non-regression lock: `--min-score` is a z-score multiplier k, and this file exists
to make a silent return to the old absolute-cosine-cutoff semantics impossible.

Every fixture here is hand-built and offline: 2-D (or 6-D) unit vectors and a ZERO
centroid, so `recentre` is the identity and each entry's score against the query
[1, 0] is exactly the number written in the fixture. That is what lets the expected
sets below be arithmetic, not guesswork — and what makes an absolute-cutoff
implementation fail loudly instead of coincidentally agreeing.
"""
from __future__ import annotations

import math
import statistics

from code_fauna_codex.cli import build_parser
from code_fauna_codex.semantic import (
    CURRENT_KEY_SCHEMA, DEFAULT_MIN_ZSCORE, DEFAULT_SIMILAR_MIN_ZSCORE,
    MIN_ENTRIES_FOR_ZSCORE, search, similar_pairs,
)

QUERY = [1.0, 0.0]
# Six scores, enough to activate the gate (>= MIN_ENTRIES_FOR_ZSCORE), spread so that
# mean and stdev are round-ish numbers: mean 0.65, population stdev ~0.170783.
SCORES = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4]
MEAN = statistics.mean(SCORES)
STDEV = statistics.pstdev(SCORES)


def _unit(score: float) -> list[float]:
    """A 2-D unit vector whose cosine with QUERY is exactly `score`."""
    return [score, math.sqrt(1.0 - score * score)]


def _search_index(scores: list[float]) -> dict:
    """Index whose entry i scores exactly scores[i] against QUERY. Entry i is named
    fn{i}, so the expected sets below read as positions in `scores`."""
    return {
        "model": "m", "dim": 2, "key_schema": CURRENT_KEY_SCHEMA, "centroid": [0.0, 0.0],
        "entries": {f"e{i}": {
            "section": "python_functions", "name": f"fn{i}", "file": "mod.py",
            "line": i + 1, "vector": _unit(score),
        } for i, score in enumerate(scores)},
    }


def test_fixture_scores_are_exactly_what_the_fixture_says():
    """Guard on the guard: if recentring ever stopped being the identity under a zero
    centroid, every expected set below would be meaningless rather than wrong."""
    hits = search(_search_index(SCORES), QUERY, top_k=len(SCORES), min_score=-99.0)
    assert [round(hit["score"], 9) for hit in hits] == SCORES


def test_candidate_is_kept_iff_score_clears_mean_plus_k_stdev():
    # k=0.5 -> cutoff = 0.65 + 0.5*0.170783 = 0.73539 -> only 0.9 and 0.8 clear it.
    hits = search(_search_index(SCORES), QUERY, top_k=len(SCORES), min_score=0.5)
    assert [hit["name"] for hit in hits] == ["fn0", "fn1"]

    cutoff = MEAN + 0.5 * STDEV
    assert [score for score in SCORES if score >= cutoff] == [0.9, 0.8]
    # The discriminator: read as an ABSOLUTE cosine cutoff, min_score=0.5 would keep
    # five of the six entries (every score >= 0.5). Two, not five, is the whole point.
    assert len([score for score in SCORES if score >= 0.5]) == 5
    assert len(hits) == 2


def test_k_zero_keeps_everything_at_or_above_the_mean():
    hits = search(_search_index(SCORES), QUERY, top_k=len(SCORES), min_score=0.0)
    assert [hit["name"] for hit in hits] == ["fn0", "fn1", "fn2"]
    kept = {round(hit["score"], 9) for hit in hits}
    assert all(score >= MEAN for score in kept)
    assert all(score < MEAN for score in SCORES if score not in kept)


def test_a_larger_k_keeps_strictly_fewer_results():
    index = _search_index(SCORES)
    counts = [len(search(index, QUERY, top_k=len(SCORES), min_score=k))
              for k in (0.0, 0.5, 1.0)]
    assert counts == [3, 2, 1]
    assert counts[0] > counts[1] > counts[2]


def test_gate_is_skipped_entirely_below_min_entries_for_zscore():
    """Under MIN_ENTRIES_FOR_ZSCORE entries the distribution is too small to trust, so
    the gate does not run at all — even an absurd k keeps every candidate."""
    scores = [0.9, 0.2, 0.1, 0.05]
    assert len(scores) < MIN_ENTRIES_FOR_ZSCORE
    hits = search(_search_index(scores), QUERY, top_k=10, min_score=99.0)
    assert len(hits) == len(scores)


def _similar_index() -> dict:
    """Six 6-D unit vectors, zero centroid. Three of them share the e0/e1 plane at
    15 deg and 50 deg, giving three non-zero pair scores (~0.966, ~0.819, ~0.643);
    the other three are mutually orthogonal, contributing twelve zero-score pairs.
    Over that 15-pair population, k=1 keeps three pairs and k=2 keeps one."""
    dim = 6

    def _axis(i: int) -> list[float]:
        vec = [0.0] * dim
        vec[i] = 1.0
        return vec

    def _in_plane(degrees: float) -> list[float]:
        vec = [0.0] * dim
        vec[0] = math.cos(math.radians(degrees))
        vec[1] = math.sin(math.radians(degrees))
        return vec

    vectors = [_in_plane(0.0), _in_plane(15.0), _in_plane(50.0),
               _axis(2), _axis(3), _axis(4)]
    return {
        "model": "m", "dim": dim, "key_schema": CURRENT_KEY_SCHEMA, "centroid": [0.0] * dim,
        "entries": {f"e{i}": {
            "section": "python_functions", "name": f"fn{i}", "file": f"mod{i}.py",
            "line": 1, "vector": vector,
        } for i, vector in enumerate(vectors)},
    }


def test_similar_uses_its_own_stricter_default_not_searchs():
    assert DEFAULT_SIMILAR_MIN_ZSCORE > DEFAULT_MIN_ZSCORE

    # The CLI must keep handing each command its own default — one shared default would
    # silently make near-duplicate detection as permissive as search.
    parser = build_parser()
    assert parser.parse_args(["search", "anything"]).min_score == DEFAULT_MIN_ZSCORE
    assert parser.parse_args(["similar"]).min_score == DEFAULT_SIMILAR_MIN_ZSCORE

    # ...and the stricter default really is stricter on the same population.
    index = _similar_index()
    loose = similar_pairs(index, DEFAULT_MIN_ZSCORE)
    strict = similar_pairs(index, DEFAULT_SIMILAR_MIN_ZSCORE)
    assert len(loose) == 3
    assert len(strict) == 1
    assert {strict[0]["a"]["name"], strict[0]["b"]["name"]} == {"fn0", "fn1"}
