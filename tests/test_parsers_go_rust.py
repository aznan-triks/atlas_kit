"""Tests — tree-sitter Go/Rust extraction. Skips cleanly when those grammars aren't
installed: they're optional packages, unlike the always-present regex backend."""
from __future__ import annotations

import pytest

from conftest import write

pytest.importorskip("tree_sitter", reason="tree-sitter extras not installed")
pytest.importorskip("tree_sitter_go", reason="tree-sitter-go not installed")
pytest.importorskip("tree_sitter_rust", reason="tree-sitter-rust not installed")

from code_fauna_codex.parsers import resolve_parser  # noqa: E402
from code_fauna_codex.parsers.treesitter_parser import TreeSitterParser  # noqa: E402

GO_SOURCE = """package widget

import "fmt"

// Greet says hello.
func Greet(name string) string {
	return fmt.Sprintf("hi %s", name)
}

type Widget struct {
	Size int
}

type Shape interface {
	Area() float64
}

func (w *Widget) Render(
	depth int,
) error {
	return nil
}

func (w Widget) Area() float64 {
	return float64(w.Size)
}
"""

RUST_SOURCE = """//! Widget crate.

/// Adds two numbers.
pub fn add(a: i32, b: i32) -> i32 {
    a + b
}

pub struct Widget {
    size: u32,
}

pub enum Shape {
    Circle,
    Square,
}

impl Widget {
    pub fn new(size: u32) -> Self {
        Widget { size }
    }

    fn hidden(&self) {}
}

trait Draw {
    fn draw(&self);
}

impl Draw for Widget {
    fn draw(&self) {}
}
"""


def _parse(tmp_path, name: str, source: str):
    path = write(tmp_path, name, source)
    return TreeSitterParser().parse(path, name)


def test_go_extracts_functions_methods_and_structs(tmp_path):
    symbols = _parse(tmp_path, "widget.go", GO_SOURCE)
    found = {(s.section, s.name) for s in symbols}
    assert ("generic_functions", "Greet") in found
    # Receiver methods carry the receiver type, pointer receiver collapsed to the type.
    assert ("generic_functions", "Widget.Render") in found
    assert ("generic_functions", "Widget.Area") in found
    assert ("generic_classes", "Widget") in found
    # Interfaces are not classes in the codex sense — deliberately not extracted.
    assert ("generic_classes", "Shape") not in found


def test_go_symbol_fields(tmp_path):
    by_name = {s.name: s for s in _parse(tmp_path, "widget.go", GO_SOURCE)}
    assert all(s.language == "go" for s in by_name.values())
    assert by_name["Greet"].line == 6
    assert by_name["Greet"].signature == "func Greet(name string) string {"
    assert by_name["Widget"].line == 10
    assert by_name["Widget"].signature == "type Widget struct {"
    # Multi-line signature: only the header line is kept, trimmed.
    assert by_name["Widget.Render"].line == 18
    assert by_name["Widget.Render"].signature == "func (w *Widget) Render("
    assert by_name["Widget.Area"].line == 24
    assert all(s.file == "widget.go" for s in by_name.values())


def test_rust_extracts_functions_impl_methods_structs_and_enums(tmp_path):
    symbols = _parse(tmp_path, "widget.rs", RUST_SOURCE)
    found = {(s.section, s.name) for s in symbols}
    assert ("generic_functions", "add") in found
    assert ("generic_functions", "Widget.new") in found
    assert ("generic_functions", "Widget.hidden") in found
    # `impl Draw for Widget` -> qualified by the type, not the trait.
    assert ("generic_functions", "Widget.draw") in found
    assert ("generic_functions", "Draw.draw") not in found
    assert ("generic_classes", "Widget") in found
    assert ("generic_classes", "Shape") in found


def test_rust_symbol_fields(tmp_path):
    by_name = {s.name: s for s in _parse(tmp_path, "widget.rs", RUST_SOURCE)}
    assert all(s.language == "rust" for s in by_name.values())
    assert by_name["add"].line == 4
    assert by_name["add"].signature == "pub fn add(a: i32, b: i32) -> i32 {"
    assert by_name["Widget"].line == 8
    assert by_name["Shape"].line == 12
    assert by_name["Widget.new"].line == 18
    assert by_name["Widget.new"].signature == "pub fn new(size: u32) -> Self {"


def test_rust_generic_impl_collapses_type_arguments(tmp_path):
    # `impl<T> Widget<T>` must name methods `Widget.get`, not `Widget<T>.get`.
    symbols = _parse(tmp_path, "generic.rs", """
pub struct Widget<T> { value: T }

impl<T> Widget<T> {
    pub fn get(&self) -> &T { &self.value }
}
""")
    found = {(s.section, s.name) for s in symbols}
    assert ("generic_classes", "Widget") in found
    assert ("generic_functions", "Widget.get") in found


def test_go_generic_receiver_collapses_type_parameters(tmp_path):
    symbols = _parse(tmp_path, "generic.go", """package widget

type Box[T any] struct {
	value T
}

func (b *Box[T]) Get() T {
	return b.value
}
""")
    found = {(s.section, s.name) for s in symbols}
    assert ("generic_classes", "Box") in found
    assert ("generic_functions", "Box.Get") in found


def test_go_grouped_type_block_keeps_per_spec_lines(tmp_path):
    # `type ( ... )` holds several specs under one declaration: each must report its
    # own line, so the signature falls back to the spec header there.
    by_name = {s.name: s for s in _parse(tmp_path, "grouped.go", """package widget

type (
	Alpha struct{}
	Beta  struct{}
)
""")}
    assert by_name["Alpha"].line == 4
    assert by_name["Beta"].line == 5
    assert by_name["Alpha"].signature == "Alpha struct{}"


def test_resolve_parser_auto_prefers_treesitter_for_go_and_rust():
    assert isinstance(resolve_parser(".go", mode="auto"), TreeSitterParser)
    assert isinstance(resolve_parser(".rs", mode="auto"), TreeSitterParser)


def test_resolve_parser_treesitter_mode_covers_go_and_rust():
    assert isinstance(resolve_parser(".go", mode="treesitter"), TreeSitterParser)
    assert isinstance(resolve_parser(".rs", mode="treesitter"), TreeSitterParser)
