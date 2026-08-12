"""Mechanical mode: walk a repository and index its code symbols. No network, no API key.

Python files are parsed precisely via `ast`. Every other supported extension falls
back to a best-effort regex scan (documented as non-exhaustive) — good enough to
answer "does something like this already exist" without pretending to be a real
per-language parser.
"""
from __future__ import annotations

import ast
import fnmatch
import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "target", ".next", ".idea", ".vscode", "vendor",
}

SUPPORTED_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs"}


@dataclass
class Symbol:
    section: str
    name: str
    file: str
    line: int
    signature: str
    docstring: str
    language: str


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


# One (regex, section) pair per extension group. Best-effort: catches the common
# declaration shapes, not every valid syntax variant of each language.
_GENERIC_PATTERNS: dict[str, list[tuple[re.Pattern, str]]] = {
    ".js": [
        (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s+([A-Za-z_$][\w$]*)"), "generic_functions"),
        (re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)"), "generic_classes"),
    ],
    ".go": [
        (re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\("), "generic_functions"),
    ],
    ".rs": [
        (re.compile(r"^\s*(?:pub\s+)?fn\s+([A-Za-z_]\w*)\s*[(<]"), "generic_functions"),
        (re.compile(r"^\s*(?:pub\s+)?struct\s+([A-Za-z_]\w*)"), "generic_classes"),
    ],
}
_GENERIC_PATTERNS[".jsx"] = _GENERIC_PATTERNS[".js"]
_GENERIC_PATTERNS[".ts"] = _GENERIC_PATTERNS[".js"]
_GENERIC_PATTERNS[".tsx"] = _GENERIC_PATTERNS[".js"]

_LANGUAGE_BY_EXT = {
    ".js": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".rs": "rust",
}


def parse_generic_file(path: Path, rel: str) -> list[Symbol]:
    patterns = _GENERIC_PATTERNS.get(path.suffix)
    if not patterns:
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return []

    out: list[Symbol] = []
    for lineno, line in enumerate(lines, start=1):
        for pattern, section in patterns:
            match = pattern.match(line)
            if match:
                out.append(Symbol(
                    section=section, name=match.group(1), file=rel, line=lineno,
                    signature=line.strip(), docstring="",
                    language=_LANGUAGE_BY_EXT[path.suffix],
                ))
    return out


def build_atlas(root: Path, ignore_globs: list[str] | None = None,
                previous: dict | None = None) -> dict:
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
            found = parse_python_file(path, rel) if path.suffix == ".py" else parse_generic_file(path, rel)
            rows = [asdict(s) for s in found]

        for row in rows:
            symbols_by_section.setdefault(row["section"], []).append(
                {k: v for k, v in row.items() if k != "section"}
            )

    return {"root": str(root), "files": files, "symbols": symbols_by_section}
