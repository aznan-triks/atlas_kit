"""Mechanical mode: walk a repository and index its code symbols. No network, no API key.

Python files are parsed precisely via `ast`. Every other supported extension is
dispatched through `atlas_kit.parsers` (regex fallback, or tree-sitter where
available) — see that package for the per-language backends.
"""
from __future__ import annotations

import ast
import fnmatch
import hashlib
from dataclasses import asdict
from pathlib import Path

from atlas_kit.parsers import PARSER_MODES, resolve_parser
from atlas_kit.parsers.regex_parser import parse_generic_file
from atlas_kit.symbol import Symbol

DEFAULT_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "target", ".next", ".idea", ".vscode", "vendor", ".claude",
}

SUPPORTED_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs"}


def should_ignore(rel_posix: str, ignore_globs: list[str]) -> bool:
    parts = rel_posix.split("/")
    if any(part in DEFAULT_IGNORE_DIRS for part in parts[:-1]):
        return True
    return any(fnmatch.fnmatch(rel_posix, glob) for glob in ignore_globs)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _iter_files(root: Path, ignore_globs: list[str]):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in SUPPORTED_EXTENSIONS:
            continue
        rel = path.relative_to(root).as_posix()
        if should_ignore(rel, ignore_globs):
            continue
        yield path, rel


def _fmt_args(args: ast.arguments) -> str:
    return ", ".join(a.arg for a in args.args)


def parse_python_file(path: Path, rel: str) -> list[Symbol]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []

    out: list[Symbol] = []
    class_stack: list[str] = []

    class Visitor(ast.NodeVisitor):
        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            out.append(Symbol(
                section="python_classes", name=node.name, file=rel, line=node.lineno,
                signature=f"class {node.name}", docstring=ast.get_docstring(node) or "",
                language="python",
            ))
            class_stack.append(node.name)
            self.generic_visit(node)
            class_stack.pop()

        def _visit_func(self, node) -> None:
            qualname = ".".join([*class_stack, node.name])
            out.append(Symbol(
                section="python_methods" if class_stack else "python_functions",
                name=qualname, file=rel, line=node.lineno,
                signature=f"def {qualname}({_fmt_args(node.args)})",
                docstring=ast.get_docstring(node) or "", language="python",
            ))
            self.generic_visit(node)

        visit_FunctionDef = _visit_func
        visit_AsyncFunctionDef = _visit_func

    Visitor().visit(tree)
    return out


def build_atlas(root: Path, ignore_globs: list[str] | None = None,
                previous: dict | None = None, parser_mode: str = "auto") -> dict:
    if parser_mode not in PARSER_MODES:
        raise ValueError(f"Unknown parser mode '{parser_mode}'. Available: {', '.join(PARSER_MODES)}")

    ignore_globs = ignore_globs or []
    prev_files: dict = (previous or {}).get("files", {})
    prev_symbols_by_file: dict[str, list[dict]] = {}
    for section, rows in (previous or {}).get("symbols", {}).items():
        for row in rows:
            prev_symbols_by_file.setdefault(row["file"], []).append({**row, "section": section})

    files: dict[str, str] = {}
    symbols_by_section: dict[str, list[dict]] = {}

    for path, rel in _iter_files(root, ignore_globs):
        digest = file_hash(path)
        files[rel] = digest

        if prev_files.get(rel) == digest:
            rows = prev_symbols_by_file.get(rel, [])
        else:
            if path.suffix == ".py":
                found = parse_python_file(path, rel)
            else:
                parser = resolve_parser(path.suffix, parser_mode)
                found = parser.parse(path, rel) if parser else []
            rows = [asdict(s) for s in found]

        for row in rows:
            symbols_by_section.setdefault(row["section"], []).append(
                {k: v for k, v in row.items() if k != "section"}
            )

    return {"root": str(root), "files": files, "symbols": symbols_by_section}
