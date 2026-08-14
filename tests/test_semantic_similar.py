"""Tests — atlas_kit.semantic.similar_pairs / cli `similar` (offline near-duplicate
report). No network, no API key — mirrors the fail-fast and z-score discipline
already pinned for search() in tests/test_semantic.py.

Synthetic fixture reuses the shared-domain-bias construction from
tests/test_semantic_centroid_bug.py: N unit vectors sharing a fixed bias b plus a
small, mutually-orthogonal per-i direction. Two entries (0 and 0b) are built to
share the SAME direction (i.e. near-duplicates) so they stand out from the rest
once recentred.
"""
from __future__ import annotations

import json

from atlas_kit.cli import main
from atlas_kit.providers.base import l2_normalize
from atlas_kit.semantic import centroid, similar_pairs

DIM = 16
N = 12
BIAS_INDEX = 15
BIAS_WEIGHT = 0.85
DIRECTION_WEIGHT = 0.15


def _one_hot(index: int, dim: int) -> list[float]:
    v = [0.0] * dim
    v[index] = 1.0
    return v


def _orthogonal_component(v: list[float], b: list[float]) -> list[float]:
    dot_vb = sum(x * y for x, y in zip(v, b))
    return [x - dot_vb * y for x, y in zip(v, b)]


def _build_index(same_file: bool) -> dict:
    """N entries: entries 0 and 1 are near-duplicates (same direction), the rest
    each get their own distinct direction. `same_file` controls whether the
    near-duplicate pair (0, 1) lives in one file or two."""
    b = _one_hot(BIAS_INDEX, DIM)
    directions = [l2_normalize(_orthogonal_component(_one_hot(i, DIM), b)) for i in range(N)]
    # Entry 1 reuses entry 0's direction (near-duplicate); everyone else keeps their own.
    directions[1] = directions[0]

    vectors = []
    for i in range(N):
        combined = [BIAS_WEIGHT * b[k] + DIRECTION_WEIGHT * directions[i][k] for k in range(DIM)]
        vectors.append(l2_normalize(combined))

    c = centroid(vectors)
    entries = {}
    for i in range(N):
        file_ = "dup.py" if (same_file and i in (0, 1)) else f"mod{i}.py"
        entries[f"e{i}"] = {
            "section": "python_functions", "name": f"fn{i}", "file": file_, "line": 1,
            "vector": vectors[i],
        }
    return {"model": "m", "dim": DIM, "centroid": c, "entries": entries}


def test_similar_pairs_finds_near_duplicate_entries():
    index = _build_index(same_file=False)
    hits = similar_pairs(index, min_score=2.0)
    assert hits, "expected at least one pair to clear the z-score gate"
    top = hits[0]
    names = {top["a"]["name"], top["b"]["name"]}
    assert names == {"fn0", "fn1"}, f"expected the near-duplicate pair on top, got {names}"


def test_similar_raises_when_index_has_no_centroid():
    index = {"entries": {
        "a": {"section": "s", "name": "x", "file": "f.py", "line": 1, "vector": [1.0, 0.0]},
        "b": {"section": "s", "name": "y", "file": "f.py", "line": 2, "vector": [0.0, 1.0]},
    }}
    try:
        similar_pairs(index, min_score=2.0)
        assert False, "expected ValueError for a centroid-less (unmigrated) index"
    except ValueError:
        pass


def _write_index(tmp_path, same_file):
    index = _build_index(same_file=same_file)
    path = tmp_path / "semantic_index.json"
    path.write_text(json.dumps(index), encoding="utf-8")
    return path


def test_cli_similar_includes_same_file_pairs_by_default(tmp_path, capsys):
    index_path = _write_index(tmp_path, same_file=True)
    code = main(["similar", "--index", str(index_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "fn0" in out and "fn1" in out
    assert "included 1 same-file pair" in out


def test_cli_similar_excludes_same_file_pairs_when_flagged(tmp_path, capsys):
    index_path = _write_index(tmp_path, same_file=True)
    code = main(["similar", "--index", str(index_path), "--exclude-same-file"])
    out = capsys.readouterr().out
    assert code == 0
    assert "excluded 1 same-file pair" in out
    # The near-duplicate pair (0, 1) is the only one in the same file — dropped.
    assert "fn0" not in out or "fn1" not in out


def test_cli_similar_reports_header_with_correct_counts(tmp_path, capsys):
    index_path = _write_index(tmp_path, same_file=False)
    code = main(["similar", "--index", str(index_path)])
    out = capsys.readouterr().out
    assert code == 0
    n_entries = N
    n_pairs = n_entries * (n_entries - 1) // 2
    assert f"Compared {n_entries} indexed entries ({n_pairs} pair(s) considered)" in out
    assert "included 0 same-file pair(s)" in out


def test_cli_similar_missing_centroid_fails_fast(tmp_path, capsys):
    index = {"model": "m", "dim": 2, "entries": {
        "a": {"section": "s", "name": "x", "file": "f.py", "line": 1, "vector": [1.0, 0.0]},
        "b": {"section": "s", "name": "y", "file": "f.py", "line": 2, "vector": [0.0, 1.0]},
    }}
    path = tmp_path / "semantic_index.json"
    path.write_text(json.dumps(index), encoding="utf-8")

    code = main(["similar", "--index", str(path)])
    err = capsys.readouterr().err
    assert code != 0
    assert "centroid" in err.lower()
