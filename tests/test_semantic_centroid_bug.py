"""Tests — reproduces the shared-domain-bias relevance bug in semantic search.

Synthetic, offline: no provider, no API key, no numpy. Each of the N=12 unit
vectors is built as 0.85 * b (a FIXED bias vector shared by every document) +
0.15 * d_i (a direction unique to document i, orthogonal to b and to every
other d_j), then L2-normalized via `providers.base.l2_normalize` — same helper
every real provider uses. This mimics a known property of real embedding
models on a small, topically-narrow corpus: most of the variance is a shared
"domain" component, which inflates cosine similarity between UNRELATED
documents far above a naive fixed threshold (DEFAULT_MIN_SCORE = 0.55).

Part 1 (must PASS today): raw cosine between unrelated docs is already high,
proving the fixed threshold can't discriminate them.

Part 2 (must FAIL today, red/TDD): `centroid`/`recentre` don't exist yet in
`fauna_codex.semantic` — this pins the contract a later phase implements
against. Do not implement them here.
"""
from __future__ import annotations

from fauna_codex.providers.base import l2_normalize
from fauna_codex.semantic import DEFAULT_MIN_SCORE, cosine

DIM = 16
N = 12
BIAS_INDEX = 15  # b lives on its own dimension, disjoint from the 12 per-i directions
BIAS_WEIGHT = 0.85
DIRECTION_WEIGHT = 0.15

UNRELATED_PAIRS = [(0, 1), (2, 7), (3, 11), (5, 9)]


def _one_hot(index: int, dim: int) -> list[float]:
    v = [0.0] * dim
    v[index] = 1.0
    return v


def _orthogonal_component(v: list[float], b: list[float]) -> list[float]:
    """v with its projection onto unit vector b removed."""
    dot_vb = sum(x * y for x, y in zip(v, b))
    return [x - dot_vb * y for x, y in zip(v, b)]


def _build_synthetic_vectors() -> list[list[float]]:
    """N unit vectors sharing a fixed bias b, differing only in a small,
    mutually-orthogonal per-i component. Mimics a narrow-domain corpus."""
    b = _one_hot(BIAS_INDEX, DIM)  # fixed shared bias, identical for every i
    # distinct one-hot basis vectors, projected orthogonal to b (already
    # orthogonal here since indices 0..11 are disjoint from BIAS_INDEX, but
    # the projection is kept explicit so the construction holds in general)
    directions = [l2_normalize(_orthogonal_component(_one_hot(i, DIM), b)) for i in range(N)]

    vectors = []
    for i in range(N):
        combined = [BIAS_WEIGHT * b[k] + DIRECTION_WEIGHT * directions[i][k] for k in range(DIM)]
        vectors.append(l2_normalize(combined))
    return vectors


def test_shared_domain_bias_inflates_cosine_above_fixed_threshold():
    """Bug confirmed: unrelated synthetic docs that only share domain bias
    already score well above 0.5 — and above DEFAULT_MIN_SCORE (0.55) — so
    today's fixed threshold cannot reliably discriminate them."""
    vectors = _build_synthetic_vectors()
    for i, j in UNRELATED_PAIRS:
        score = cosine(vectors[i], vectors[j])
        assert score > 0.5, f"pair ({i}, {j}) scored {score:.4f}, expected > 0.5"
        assert score > DEFAULT_MIN_SCORE, (
            f"pair ({i}, {j}) scored {score:.4f}, expected > DEFAULT_MIN_SCORE ({DEFAULT_MIN_SCORE})"
        )


def test_recentre_removes_shared_domain_bias():
    """Expected fix (not yet implemented): subtracting the corpus centroid
    before comparing should collapse unrelated-pair similarity toward zero.

    RED on purpose -- `centroid`/`recentre` don't exist in fauna_codex.semantic
    yet. This pins the contract a later phase implements:
      centroid(vectors: list[list[float]]) -> list[float]        # mean vector, NOT renormalized
      recentre(vector: list[float], centroid: list[float]) -> list[float]  # subtract centroid, then l2_normalize
    """
    vectors = _build_synthetic_vectors()

    from fauna_codex.semantic import centroid, recentre  # noqa: expected to fail today (ImportError/AttributeError)

    c = centroid(vectors)
    for i, j in UNRELATED_PAIRS:
        vi_r = recentre(vectors[i], c)
        vj_r = recentre(vectors[j], c)
        score = cosine(vi_r, vj_r)
        assert abs(score) < 0.2, f"recentred pair ({i}, {j}) scored {score:.4f}, expected |score| < 0.2"
