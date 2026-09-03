"""Tests — fauna_codex.parsers (registry + regex/treesitter backends)."""
from __future__ import annotations

import pytest

from conftest import write

from fauna_codex.parsers import PARSERS, resolve_parser
from fauna_codex.parsers.regex_parser import RegexParser
from fauna_codex.parsers import treesitter_parser as ts_module
from fauna_codex.parsers.treesitter_parser import TREESITTER_AVAILABLE, TreeSitterParser
from fauna_codex.scan import build_atlas

needs_treesitter = pytest.mark.skipif(not TREESITTER_AVAILABLE, reason="tree-sitter extras not installed")


def _hide_grammar(monkeypatch, extension: str) -> None:
    """Simulate a machine without `extension`'s grammar package installed, without
    touching the real environment (never uninstall anything from a test)."""
    monkeypatch.delitem(ts_module._TS_LANGUAGE_BY_EXT, extension, raising=False)


def test_resolve_parser_regex_mode_ignores_treesitter():
    assert isinstance(resolve_parser(".js", mode="regex"), RegexParser)


def test_resolve_parser_unsupported_extension_returns_none():
    assert resolve_parser(".xyz", mode="regex") is None
    assert resolve_parser(".xyz", mode="auto") is None


def test_resolve_parser_treesitter_mode_raises_for_missing_go_grammar(monkeypatch):
    # Fail Fast: an explicit --parser treesitter on .go must name tree-sitter-go, never
    # silently degrade to regex. Grammars are per-extension pip packages now.
    _hide_grammar(monkeypatch, ".go")
    with pytest.raises(RuntimeError, match="tree-sitter-go"):
        resolve_parser(".go", mode="treesitter")


def test_resolve_parser_treesitter_mode_raises_for_missing_rust_grammar(monkeypatch):
    _hide_grammar(monkeypatch, ".rs")
    with pytest.raises(RuntimeError, match="tree-sitter-rust"):
        resolve_parser(".rs", mode="treesitter")


def test_resolve_parser_auto_falls_back_to_regex_for_missing_grammar(monkeypatch):
    # Per-extension availability: a missing .go grammar must not disable .js.
    _hide_grammar(monkeypatch, ".go")
    assert isinstance(resolve_parser(".go", mode="auto"), RegexParser)


def test_resolve_parser_treesitter_mode_has_no_scope_over_unknown_extension():
    # tree-sitter knows nothing about .py here (scan.py owns Python via ast), so the
    # flag has no scope: no raise, just whatever else covers the extension — nothing.
    assert resolve_parser(".py", mode="treesitter") is None


def test_resolve_parser_regex_mode_still_serves_go():
    assert isinstance(resolve_parser(".go", mode="regex"), RegexParser)


def test_resolve_parser_treesitter_mode_raises_when_unavailable(monkeypatch):
    monkeypatch.setattr(PARSERS["treesitter"], "available", False)
    with pytest.raises(RuntimeError, match="pip install"):
        resolve_parser(".js", mode="treesitter")


def test_build_atlas_unknown_parser_mode_raises(tmp_path):
    with pytest.raises(ValueError, match="Unknown parser mode"):
        build_atlas(tmp_path, parser_mode="bogus")


@needs_treesitter
def test_resolve_parser_auto_prefers_treesitter_for_js():
    assert isinstance(resolve_parser(".js", mode="auto"), TreeSitterParser)


@needs_treesitter
def test_treesitter_parser_extracts_function_class_and_methods(tmp_path):
    path = write(tmp_path, "mod.js", """
export function greet(name) {
  return `hi ${name}`;
}

class Widget {
  render() {}
}

const add = (a, b) => a + b;
""")
    symbols = TreeSitterParser().parse(path, "mod.js")
    by_name = {(s.section, s.name) for s in symbols}
    assert ("generic_functions", "greet") in by_name
    assert ("generic_classes", "Widget") in by_name
    assert ("generic_methods", "Widget.render") in by_name
    assert ("generic_functions", "add") in by_name


@needs_treesitter
def test_treesitter_parser_extracts_typescript_generic_class(tmp_path):
    # TS class names with type parameters (`class Widget<T>`) parse as a
    # type_identifier node, not identifier — this is the regression the JS-only
    # test above wouldn't catch.
    path = write(tmp_path, "mod.ts", """
export class Widget<T> {
  render(): void {}
}
""")
    symbols = TreeSitterParser().parse(path, "mod.ts")
    by_name = {(s.section, s.name) for s in symbols}
    assert ("generic_classes", "Widget") in by_name
    assert ("generic_methods", "Widget.render") in by_name


def test_treesitter_parser_raises_when_unavailable(monkeypatch, tmp_path):
    parser = TreeSitterParser()
    monkeypatch.setattr(parser, "available", False)
    with pytest.raises(RuntimeError, match="pip install"):
        parser.parse(tmp_path / "mod.js", "mod.js")


def test_build_atlas_parser_mode_regex_never_extracts_js_methods(tmp_path):
    write(tmp_path, "mod.js", "class Widget {\n  render() {}\n}\n")
    atlas = build_atlas(tmp_path, parser_mode="regex")
    sections = {section for section, rows in atlas["symbols"].items() if rows}
    assert "generic_classes" in sections
    assert "generic_methods" not in sections  # regex_parser never extracted JS methods


@needs_treesitter
def test_build_atlas_parser_mode_auto_extracts_js_methods(tmp_path):
    write(tmp_path, "mod.js", "class Widget {\n  render() {}\n}\n")
    atlas = build_atlas(tmp_path, parser_mode="auto")
    names = {(section, s["name"]) for section, rows in atlas["symbols"].items() for s in rows}
    assert ("generic_methods", "Widget.render") in names
