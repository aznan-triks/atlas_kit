"""Tests — code_fauna_codex.scan (mechanical mode, zero network, zero API key)."""
from __future__ import annotations

from conftest import write

from code_fauna_codex.scan import build_codex, parse_generic_file, parse_python_file, should_ignore


def test_parse_python_file_extracts_function_and_docstring(tmp_path):
    path = write(tmp_path, "mod.py", '''
def add(a, b):
    """Sum two numbers."""
    return a + b
''')
    symbols = parse_python_file(path, "mod.py")
    funcs = [s for s in symbols if s.section == "python_functions"]
    assert len(funcs) == 1
    assert funcs[0].name == "add"
    assert funcs[0].docstring == "Sum two numbers."
    assert funcs[0].line == 2


def test_parse_python_file_qualifies_methods_with_class_name(tmp_path):
    path = write(tmp_path, "mod.py", '''
class Greeter:
    def hello(self):
        """Say hi."""
        return "hi"
''')
    symbols = parse_python_file(path, "mod.py")
    classes = [s for s in symbols if s.section == "python_classes"]
    methods = [s for s in symbols if s.section == "python_methods"]
    assert classes[0].name == "Greeter"
    assert methods[0].name == "Greeter.hello"


def test_parse_python_file_survives_syntax_error(tmp_path):
    path = write(tmp_path, "broken.py", "def bad(:\n")
    assert parse_python_file(path, "broken.py") == []


def test_parse_generic_file_extracts_js_function_and_class(tmp_path):
    path = write(tmp_path, "mod.js", '''
export function greet(name) {
  return `hi ${name}`;
}

class Widget {
  render() {}
}
''')
    symbols = parse_generic_file(path, "mod.js")
    names = {(s.section, s.name) for s in symbols}
    assert ("generic_functions", "greet") in names
    assert ("generic_classes", "Widget") in names


def test_parse_generic_file_extracts_go_func(tmp_path):
    path = write(tmp_path, "main.go", "func Add(a, b int) int {\n\treturn a + b\n}\n")
    symbols = parse_generic_file(path, "main.go")
    assert any(s.name == "Add" and s.section == "generic_functions" for s in symbols)


def test_parse_generic_file_unknown_extension_returns_empty(tmp_path):
    path = write(tmp_path, "data.bin", "not code")
    assert parse_generic_file(path, "data.bin") == []


def test_should_ignore_default_dirs():
    assert should_ignore("node_modules/pkg/index.js", [])
    assert should_ignore(".git/HEAD", [])
    assert should_ignore(".claude/skills/foo/SKILL.md", [])
    assert not should_ignore("src/main.py", [])


def test_should_ignore_custom_glob():
    # "third_party" is not in DEFAULT_IGNORE_DIRS — this exercises the --ignore glob
    # path specifically, not the built-in directory denylist.
    assert should_ignore("third_party/lib.py", ["third_party/*"])
    assert not should_ignore("src/third_party.py", ["third_party/*"])


def test_build_codex_scans_python_and_js(tmp_path):
    write(tmp_path, "a.py", "def foo():\n    pass\n")
    write(tmp_path, "b.js", "function bar() {}\n")
    write(tmp_path, "node_modules/skip.js", "function skipped() {}\n")

    codex = build_codex(tmp_path)

    names = {s["name"] for section in codex["symbols"].values() for s in section}
    assert "foo" in names
    assert "bar" in names
    assert "skipped" not in names
    assert set(codex["files"]) == {"a.py", "b.js"}


def test_build_codex_incremental_skips_unchanged_files(tmp_path):
    write(tmp_path, "a.py", "def foo():\n    pass\n")
    first = build_codex(tmp_path)

    # Second scan, same content: reusing `previous` must not re-touch a.py's entry
    # (verified indirectly — the hash for a.py stays identical across runs).
    second = build_codex(tmp_path, previous=first)
    assert second["files"]["a.py"] == first["files"]["a.py"]


def test_build_codex_incremental_updates_changed_file(tmp_path):
    write(tmp_path, "a.py", "def foo():\n    pass\n")
    first = build_codex(tmp_path)
    write(tmp_path, "a.py", "def foo():\n    pass\n\ndef bar():\n    pass\n")
    second = build_codex(tmp_path, previous=first)

    names = {s["name"] for section in second["symbols"].values() for s in section}
    assert {"foo", "bar"} <= names
    assert second["files"]["a.py"] != first["files"]["a.py"]
