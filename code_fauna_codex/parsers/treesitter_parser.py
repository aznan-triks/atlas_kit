"""Tree-sitter based parser for JS/JSX/TS/TSX, Go and Rust — a real AST instead of
regex_parser's best-effort line matching: catches multi-line signatures, arrow functions
assigned to a const, class methods, Go receiver methods and Rust impl methods (none of
which regex_parser ever extracted).

Optional dependency: pip install 'code-fauna-codex[treesitter]'. Each language grammar is its
own pip package, so availability is PER EXTENSION, not per backend: a machine can have
tree-sitter-javascript and not tree-sitter-go. `available` therefore only means "at least
one grammar imported"; `supports(extension)` is the real check. Import failure is recorded,
never acted on — the registry (code_fauna_codex.parsers) decides what a missing grammar means for
a given --parser mode, this module never falls back on its own.
"""
from __future__ import annotations

import importlib
from pathlib import Path

from code_fauna_codex.symbol import Symbol

try:
    from tree_sitter import Language, Parser as _TSParser
    _CORE_AVAILABLE = True
except ImportError:
    _CORE_AVAILABLE = False

# Single source of truth: grammar pip package -> {extension: factory attribute on the
# imported module}. The pip-package table below is derived from it, so adding a language
# means adding exactly one row here.
_GRAMMARS: tuple[tuple[str, dict[str, str]], ...] = (
    ("tree_sitter_javascript", {".js": "language", ".jsx": "language"}),
    ("tree_sitter_typescript", {".ts": "language_typescript", ".tsx": "language_tsx"}),
    ("tree_sitter_go", {".go": "language"}),
    ("tree_sitter_rust", {".rs": "language"}),
)

_PIP_PACKAGE_BY_EXT: dict[str, str] = {
    extension: module.replace("_", "-")
    for module, entrypoints in _GRAMMARS
    for extension in entrypoints
}

_LANGUAGE_NAME_BY_EXT = {
    ".js": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".rs": "rust",
}

# Populated grammar by grammar: one missing package must not take the others down with
# it, which a single try/except around every import would have done.
_TS_LANGUAGE_BY_EXT: dict[str, object] = {}
if _CORE_AVAILABLE:
    for _module_name, _entrypoints in _GRAMMARS:
        try:
            _module = importlib.import_module(_module_name)
        except ImportError:
            continue  # grammar not installed — its extensions simply stay unsupported
        for _extension, _factory in _entrypoints.items():
            _TS_LANGUAGE_BY_EXT[_extension] = Language(getattr(_module, _factory)())

TREESITTER_AVAILABLE = bool(_TS_LANGUAGE_BY_EXT)

# Class/function names are "identifier" in JS, "type_identifier" in TS/TSX.
_NAME_NODE_TYPES = ("identifier", "type_identifier")


def missing_grammar_message(extension: str) -> str:
    """Fail-Fast message naming the exact package to install for `extension`."""
    package = _PIP_PACKAGE_BY_EXT.get(extension, "tree-sitter")
    return (
        f"--parser treesitter requested for '{extension}' but its tree-sitter grammar "
        f"isn't installed — pip install {package} "
        f"(or pip install 'code-fauna-codex[treesitter]' for every grammar)."
    )


def _child_name(node) -> str | None:
    for child in node.children:
        if child.type in _NAME_NODE_TYPES:
            return child.text.decode("utf-8")
    return None


def _field_name(node) -> str | None:
    """Name via the grammar's own `name` field. Go and Rust both expose it, so there is
    no need for the JS `_child_name` heuristic of scanning for the first identifier."""
    name_node = node.child_by_field_name("name")
    return name_node.text.decode("utf-8") if name_node is not None else None


def _first_type_identifier(node) -> str | None:
    """Bare type name under `node`, digging through pointer/generic wrappers.

    Go `func (w *Widget)` and Rust `impl Widget<T>` both wrap the type we want to name
    the method after; collapsing them to `Widget` makes the qualname read like Python's
    `Class.method`, which is what makes a symbol findable by name.
    """
    if node is None:
        return None
    if node.type == "type_identifier":
        return node.text.decode("utf-8")
    for child in node.children:
        found = _first_type_identifier(child)
        if found:
            return found
    return None


# docstring is left empty for every tree-sitter language: doc comments are sibling
# `comment`/`line_comment` nodes, not children of the declaration, so associating them
# means walking backwards over an arbitrary run of siblings — a comment-association
# engine, deliberately out of scope here.
def _symbol(name: str, rel: str, node, language: str, section: str) -> Symbol:
    signature = node.text.decode("utf-8").splitlines()[0].strip()
    return Symbol(section=section, name=name, file=rel, line=node.start_point.row + 1,
                 signature=signature, docstring="", language=language)


def _walk_js(node, rel: str, language: str, out: list[Symbol], class_name: str | None = None) -> None:
    for child in node.children:
        if child.type == "function_declaration":
            name = _child_name(child)
            if name:
                qualname = f"{class_name}.{name}" if class_name else name
                section = "generic_methods" if class_name else "generic_functions"
                out.append(_symbol(qualname, rel, child, language, section))
            _walk_js(child, rel, language, out, class_name)
        elif child.type == "class_declaration":
            name = _child_name(child) or "?"
            out.append(_symbol(name, rel, child, language, "generic_classes"))
            _walk_js(child, rel, language, out, class_name=name)
        elif child.type == "method_definition":
            name = None
            for grandchild in child.children:
                if grandchild.type == "property_identifier":
                    name = grandchild.text.decode("utf-8")
                    break
            if name and class_name:
                out.append(_symbol(f"{class_name}.{name}", rel, child, language, "generic_methods"))
            _walk_js(child, rel, language, out, class_name)
        elif child.type == "variable_declarator":
            has_function_value = any(c.type in ("arrow_function", "function_expression") for c in child.children)
            if has_function_value:
                name = _child_name(child)
                if name:
                    qualname = f"{class_name}.{name}" if class_name else name
                    section = "generic_methods" if class_name else "generic_functions"
                    out.append(_symbol(qualname, rel, child, language, section))
            _walk_js(child, rel, language, out, class_name)
        else:
            _walk_js(child, rel, language, out, class_name)


def _walk_go(node, rel: str, language: str, out: list[Symbol]) -> None:
    for child in node.children:
        if child.type == "function_declaration":
            name = _field_name(child)
            if name:
                out.append(_symbol(name, rel, child, language, "generic_functions"))
        elif child.type == "method_declaration":
            # Receiver methods land in generic_functions like plain funcs — Go has no
            # class, the receiver only qualifies the name (`Widget.Render`).
            name = _field_name(child)
            if name:
                receiver = _first_type_identifier(child.child_by_field_name("receiver"))
                qualname = f"{receiver}.{name}" if receiver else name
                out.append(_symbol(qualname, rel, child, language, "generic_functions"))
        elif child.type == "type_declaration":
            specs = [spec for spec in child.children if spec.type == "type_spec"]
            for spec in specs:
                kind = spec.child_by_field_name("type")
                name = _field_name(spec)
                # Only structs: interfaces and type aliases aren't "classes" in any
                # sense the codex uses.
                if not name or kind is None or kind.type != "struct_type":
                    continue
                # `type Widget struct {` reads better than the bare spec header
                # `Widget struct {`, but only a lone spec owns the `type` keyword —
                # inside a grouped `type ( ... )` block the spec's own line is the
                # honest one, and the only one with the right line number.
                node = child if len(specs) == 1 else spec
                out.append(_symbol(name, rel, node, language, "generic_classes"))
        else:
            _walk_go(child, rel, language, out)


def _walk_rust(node, rel: str, language: str, out: list[Symbol], impl_type: str | None = None) -> None:
    for child in node.children:
        if child.type == "function_item":
            name = _field_name(child)
            if name:
                qualname = f"{impl_type}.{name}" if impl_type else name
                out.append(_symbol(qualname, rel, child, language, "generic_functions"))
        elif child.type in ("struct_item", "enum_item"):
            name = _field_name(child)
            if name:
                out.append(_symbol(name, rel, child, language, "generic_classes"))
        elif child.type == "impl_item":
            # In `impl Trait for Type` the `type` field is Type and `trait` is Trait:
            # methods are qualified by the type they run on, never by the trait.
            impl_name = _first_type_identifier(child.child_by_field_name("type"))
            _walk_rust(child, rel, language, out, impl_type=impl_name)
        else:
            _walk_rust(child, rel, language, out, impl_type)


_WALKER_BY_LANGUAGE = {
    "javascript": _walk_js,
    "typescript": _walk_js,
    "go": _walk_go,
    "rust": _walk_rust,
}


class TreeSitterParser:
    name = "treesitter"
    available = TREESITTER_AVAILABLE  # "at least one grammar" — see supports() for the real check
    extensions = set(_LANGUAGE_NAME_BY_EXT)  # every extension this backend knows, installed or not

    def supports(self, extension: str) -> bool:
        """True only if this exact extension's grammar is importable right now.

        Reads self.available so a caller (or a test) disabling the whole backend still
        disables every extension.
        """
        return self.available and extension in _TS_LANGUAGE_BY_EXT

    def parse(self, path: Path, rel: str) -> list[Symbol]:
        extension = path.suffix
        if not self.supports(extension):
            raise RuntimeError(missing_grammar_message(extension))
        language = _TS_LANGUAGE_BY_EXT[extension]
        try:
            source = path.read_bytes()
        except OSError:
            return []
        tree = _TSParser(language).parse(source)
        out: list[Symbol] = []
        language_name = _LANGUAGE_NAME_BY_EXT[extension]
        _WALKER_BY_LANGUAGE[language_name](tree.root_node, rel, language_name, out)
        return out
