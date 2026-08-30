"""Tree-sitter based parser for JS/JSX/TS/TSX — a real AST instead of regex_parser's
best-effort line matching: catches multi-line signatures, arrow functions assigned to
a const, and class methods (which regex_parser never extracted for these languages).

Optional dependency: pip install 'atlas-kit[treesitter]'. Import failure just flips
TREESITTER_AVAILABLE to False — the registry (atlas_kit.parsers) decides what that
means for a given --parser mode, this module never falls back on its own.
"""
from __future__ import annotations

from pathlib import Path

from atlas_kit.symbol import Symbol

try:
    from tree_sitter import Language, Parser as _TSParser
    import tree_sitter_javascript as _tsjavascript
    import tree_sitter_typescript as _tstypescript

    _JS_LANGUAGE = Language(_tsjavascript.language())
    _TS_LANGUAGE = Language(_tstypescript.language_typescript())
    _TSX_LANGUAGE = Language(_tstypescript.language_tsx())
    TREESITTER_AVAILABLE = True
except ImportError:
    TREESITTER_AVAILABLE = False

_LANGUAGE_NAME_BY_EXT = {".js": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript"}
_TS_LANGUAGE_BY_EXT = (
    {".js": _JS_LANGUAGE, ".jsx": _JS_LANGUAGE, ".ts": _TS_LANGUAGE, ".tsx": _TSX_LANGUAGE}
    if TREESITTER_AVAILABLE else {}
)

# Class/function names are "identifier" in JS, "type_identifier" in TS/TSX.
_NAME_NODE_TYPES = ("identifier", "type_identifier")


def _child_name(node) -> str | None:
    for child in node.children:
        if child.type in _NAME_NODE_TYPES:
            return child.text.decode("utf-8")
    return None


def _symbol(name: str, rel: str, node, language: str, section: str) -> Symbol:
    signature = node.text.decode("utf-8").splitlines()[0].strip()
    return Symbol(section=section, name=name, file=rel, line=node.start_point.row + 1,
                 signature=signature, docstring="", language=language)


def _walk(node, rel: str, language: str, out: list[Symbol], class_name: str | None = None) -> None:
    for child in node.children:
        if child.type == "function_declaration":
            name = _child_name(child)
            if name:
                qualname = f"{class_name}.{name}" if class_name else name
                section = "generic_methods" if class_name else "generic_functions"
                out.append(_symbol(qualname, rel, child, language, section))
            _walk(child, rel, language, out, class_name)
        elif child.type == "class_declaration":
            name = _child_name(child) or "?"
            out.append(_symbol(name, rel, child, language, "generic_classes"))
            _walk(child, rel, language, out, class_name=name)
        elif child.type == "method_definition":
            name = None
            for grandchild in child.children:
                if grandchild.type == "property_identifier":
                    name = grandchild.text.decode("utf-8")
                    break
            if name and class_name:
                out.append(_symbol(f"{class_name}.{name}", rel, child, language, "generic_methods"))
            _walk(child, rel, language, out, class_name)
        elif child.type == "variable_declarator":
            has_function_value = any(c.type in ("arrow_function", "function_expression") for c in child.children)
            if has_function_value:
                name = _child_name(child)
                if name:
                    qualname = f"{class_name}.{name}" if class_name else name
                    section = "generic_methods" if class_name else "generic_functions"
                    out.append(_symbol(qualname, rel, child, language, section))
            _walk(child, rel, language, out, class_name)
        else:
            _walk(child, rel, language, out, class_name)


class TreeSitterParser:
    name = "treesitter"
    available = TREESITTER_AVAILABLE
    extensions = set(_LANGUAGE_NAME_BY_EXT)

    def parse(self, path: Path, rel: str) -> list[Symbol]:
        if not self.available:
            raise RuntimeError("tree-sitter not installed — pip install 'atlas-kit[treesitter]'.")
        language = _TS_LANGUAGE_BY_EXT[path.suffix]
        try:
            source = path.read_bytes()
        except OSError:
            return []
        tree = _TSParser(language).parse(source)
        out: list[Symbol] = []
        _walk(tree.root_node, rel, _LANGUAGE_NAME_BY_EXT[path.suffix], out)
        return out
