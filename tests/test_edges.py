"""Tests — atlas_kit.edges (Python-only call/import edges, zero network, zero API key)."""
from __future__ import annotations

import pytest
from conftest import write

from atlas_kit.edges import (
    callees_of, callers_of, parse_python_edges, unreferenced_symbols,
)
from atlas_kit.scan import build_atlas

SAMPLE = '''
import os
import a.b
from pkg.mod import helper
from . import sibling
from .rel import thing


def top():
    helper()
    os.path.join("a", "b")


class Greeter:
    def hello(self):
        top()
        top()

    def bye(self):
        self.hello()


Greeter()
'''


@pytest.fixture
def sample_edges(tmp_path):
    path = write(tmp_path, "mod.py", SAMPLE)
    return parse_python_edges(path, "mod.py")


def test_imports_record_dotted_module_names(sample_edges):
    imports, _ = sample_edges
    assert imports == [".rel", ".sibling", "a.b", "os", "pkg.mod"]


def test_imports_are_sorted_and_deduped(tmp_path):
    path = write(tmp_path, "dup.py", "import os\nimport os\nfrom zzz import a\n")
    imports, _ = parse_python_edges(path, "dup.py")
    assert imports == ["os", "zzz"]


def test_module_level_call_is_attributed_to_module(sample_edges):
    _, calls = sample_edges
    assert {"file": "mod.py", "caller": "<module>", "callee": "Greeter", "line": 23} in calls


def test_method_caller_uses_class_qualname(sample_edges):
    _, calls = sample_edges
    callers = {(row["caller"], row["callee"]) for row in calls}
    assert ("Greeter.hello", "top") in callers
    assert ("Greeter.bye", "hello") in callers


def test_function_caller_and_attribute_callee_last_segment(sample_edges):
    _, calls = sample_edges
    callers = {(row["caller"], row["callee"]) for row in calls}
    assert ("top", "helper") in callers
    # `os.path.join(...)` keeps only "join" — the last segment is what can match a symbol name.
    assert ("top", "join") in callers


def test_repeated_call_is_deduped_keeping_first_line(sample_edges):
    _, calls = sample_edges
    hits = [row for row in calls if row["caller"] == "Greeter.hello" and row["callee"] == "top"]
    assert len(hits) == 1
    assert hits[0]["line"] == 16


@pytest.mark.parametrize("source", ["def bad(:\n", "class ((:\n"])
def test_unparseable_file_yields_no_edges(tmp_path, source):
    path = write(tmp_path, "broken.py", source)
    assert parse_python_edges(path, "broken.py") == ([], [])


def test_call_without_static_name_is_dropped(tmp_path):
    path = write(tmp_path, "dyn.py", 'def f(d):\n    d["k"]()\n    f(1)()\n')
    _, calls = parse_python_edges(path, "dyn.py")
    assert [row["callee"] for row in calls] == ["f"]


def test_build_atlas_exposes_edges_block(tmp_path):
    write(tmp_path, "mod.py", SAMPLE)
    write(tmp_path, "other.js", "function bar() { baz(); }\n")

    atlas = build_atlas(tmp_path)

    assert atlas["edges"]["language"] == "python"
    assert atlas["edges"]["imports"]["mod.py"] == [".rel", ".sibling", "a.b", "os", "pkg.mod"]
    # JavaScript gets symbols but never edges — documented limitation.
    assert "other.js" not in atlas["edges"]["imports"]
    assert all(row["file"] == "mod.py" for row in atlas["edges"]["calls"])


def test_build_atlas_calls_are_sorted_and_unique(tmp_path):
    write(tmp_path, "a.py", SAMPLE)
    write(tmp_path, "b.py", "def z():\n    top()\n")

    calls = build_atlas(tmp_path)["edges"]["calls"]
    keys = [(row["file"], row["caller"], row["callee"]) for row in calls]
    assert keys == sorted(keys)
    assert len(keys) == len(set(keys))


def test_callers_of_matches_callee_last_segment(tmp_path):
    write(tmp_path, "mod.py", SAMPLE)
    atlas = build_atlas(tmp_path)

    hits = callers_of(atlas, "top")
    assert {row["caller"] for row in hits} == {"Greeter.hello"}
    # A dotted argument is reduced to its last segment before comparing.
    assert callers_of(atlas, "pkg.mod.top") == hits
    assert callers_of(atlas, "nothing_here") == []


def test_callees_of_matches_exact_qualname_or_last_segment(tmp_path):
    write(tmp_path, "mod.py", SAMPLE)
    atlas = build_atlas(tmp_path)

    assert {row["callee"] for row in callees_of(atlas, "Greeter.hello")} == {"top"}
    assert {row["callee"] for row in callees_of(atlas, "hello")} == {"top"}
    assert {row["callee"] for row in callees_of(atlas, "<module>")} == {"Greeter"}
    assert callees_of(atlas, "nothing_here") == []


def test_unreferenced_symbols_reports_never_called_python_symbols(tmp_path):
    write(tmp_path, "mod.py", "def used():\n    pass\n\n\ndef unused():\n    used()\n")
    atlas = build_atlas(tmp_path)

    names = {row["name"] for row in unreferenced_symbols(atlas)}
    assert names == {"unused"}


def test_unreferenced_symbols_excludes_dunders_tests_and_main(tmp_path):
    write(tmp_path, "mod.py", '''
class Thing:
    def __init__(self):
        pass


def test_something():
    pass


def main():
    pass


def orphan():
    pass
''')
    atlas = build_atlas(tmp_path)

    names = {row["name"] for row in unreferenced_symbols(atlas)}
    assert names == {"Thing", "orphan"}


def test_unreferenced_symbols_ignores_non_python_symbols(tmp_path):
    write(tmp_path, "mod.js", "function lonely() {}\n")
    atlas = build_atlas(tmp_path)
    assert unreferenced_symbols(atlas) == []


def test_unreferenced_symbols_sorted_by_file_then_line(tmp_path):
    write(tmp_path, "b.py", "def b1():\n    pass\n\n\ndef b2():\n    pass\n")
    write(tmp_path, "a.py", "def a1():\n    pass\n")
    atlas = build_atlas(tmp_path)

    rows = unreferenced_symbols(atlas)
    assert [(row["file"], row["line"]) for row in rows] == [("a.py", 1), ("b.py", 1), ("b.py", 5)]
    assert rows[0]["section"] == "python_functions"
