"""Mechanical mode: walk a repository and index its code symbols. No network, no API key.

Python files are parsed precisely via `ast`. Every other supported extension is
dispatched through `fauna_codex.parsers` (regex fallback, or tree-sitter where
available) — see that package for the per-language backends.

Call/import edges live in `fauna_codex.edges` and cover Python only; this module just
wires them into the atlas, under the same incremental-hash cache as symbols.
"""
from __future__ import annotations

import ast
import fnmatch
import hashlib
from dataclasses import asdict
from pathlib import Path

from fauna_codex.edges import assemble_edges, parse_python_edges, previous_edges_by_file
from fauna_codex.index_store import ATLAS_SCHEMA_VERSION
from fauna_codex.parsers import PARSER_MODES, resolve_parser
from fauna_codex.parsers.regex_parser import parse_generic_file
from fauna_codex.symbol import Symbol

IGNORE_FILE = ".faunacodexignore"

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


def read_ignore_file(root: Path) -> list[str]:
    """Globs declared in `<root>/.faunacodexignore` — one per line, `#` comments and blank
    lines skipped, each matched exactly like an `--ignore` glob.

    Always a UNION with `--ignore`, never a replacement: a repo-wide file must not be able
    to silently re-include what a caller explicitly excluded on the command line.
    A missing file is a no-op, not an error — most repos will not have one.
    """
    path = root / IGNORE_FILE
    if not path.exists():
        return []
    globs = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            globs.append(line)
    return globs


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

    ignore_globs = [*(ignore_globs or []), *read_ignore_file(root)]
    prev_files: dict = (previous or {}).get("files", {})
    prev_symbols_by_file: dict[str, list[dict]] = {}
    for section, rows in (previous or {}).get("symbols", {}).items():
        for row in rows:
            prev_symbols_by_file.setdefault(row["file"], []).append({**row, "section": section})
    prev_imports_by_file, prev_calls_by_file = previous_edges_by_file(previous)

    files: dict[str, str] = {}
    symbols_by_section: dict[str, list[dict]] = {}
    imports_by_file: dict[str, list[str]] = {}
    calls_by_file: dict[str, list[dict]] = {}

    for path, rel in _iter_files(root, ignore_globs):
        digest = file_hash(path)
        files[rel] = digest
        unchanged = prev_files.get(rel) == digest

        if unchanged:
            rows = prev_symbols_by_file.get(rel, [])
        else:
            if path.suffix == ".py":
                found = parse_python_file(path, rel)
            else:
                parser = resolve_parser(path.suffix, parser_mode)
                found = parser.parse(path, rel) if parser else []
            rows = [asdict(s) for s in found]

        if path.suffix == ".py":
            # Edges honour the same hash cache as symbols: an unchanged file is never
            # re-parsed, and its stored edges are already deduped and sorted, so reusing
            # them verbatim also preserves byte-stability of atlas.json.
            if unchanged:
                imports, calls = prev_imports_by_file.get(rel, []), prev_calls_by_file.get(rel, [])
            else:
                imports, calls = parse_python_edges(path, rel)
            if imports:
                imports_by_file[rel] = imports
            if calls:
                calls_by_file[rel] = calls

        for row in rows:
            symbols_by_section.setdefault(row["section"], []).append(
                {k: v for k, v in row.items() if k != "section"}
            )

    return {"root": str(root), "schema_version": ATLAS_SCHEMA_VERSION, "files": files,
            "symbols": symbols_by_section,
            "edges": assemble_edges(imports_by_file, calls_by_file)}
