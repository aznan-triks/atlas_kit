"""Call/import edges between symbols — Python only, stdlib `ast`, no network, no API key.

Only `.py` files get edges. Every other language stays symbols-only: a regex backend
cannot tell a call from a mention, and shipping wrong edges is worse than shipping none.
That is a documented limitation, not a bug.

Shape stored under `atlas["edges"]`:

    {"language": "python",
     "imports": {"<rel/posix.py>": ["os", "fauna_codex.scan", ...]},
     "calls": [{"file": ..., "caller": ..., "callee": ..., "line": ...}, ...]}
"""
from __future__ import annotations

import ast
from pathlib import Path

EDGES_LANGUAGE = "python"

# Excluded from `unreferenced_symbols` because their call sites are structurally invisible
# to a static scan: dunders are invoked by the interpreter, `test_*` by the test runner,
# `main` by an entry point. Reporting them would drown the real signal.
_UNREFERENCED_EXCLUDED_EXACT = {"main"}
_UNREFERENCED_EXCLUDED_PREFIX = "test_"


def _last_segment(dotted: str) -> str:
    return dotted.rsplit(".", 1)[-1]


def _callee_name(func: ast.expr) -> str | None:
    """Last name of a call target: `foo()` -> "foo", `a.b.foo()` -> "foo".

    Only the last segment is stored, deliberately. Atlas symbol names are qualnames
    (`Class.method`, `func`) whose last segment is the bare name, so comparing last
    segments is what makes a call match a symbol WITHOUT full name resolution — which
    would need import graphs, aliases and type inference we refuse to build here.
    Anything with no static name (`f()()`, `d["k"]()`) yields None and is dropped.
    """
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _import_names(node: ast.Import | ast.ImportFrom) -> list[str]:
    """Dotted module names an import statement refers to.

    `import a.b` and `from a.b import c` both record "a.b" — the module, not the member,
    because that is the file-to-file dependency. Relative imports keep their leading dots
    (`from .mod import y` -> ".mod"); `from . import x` has no module part, so the member
    name completes it (-> ".x") rather than degenerating to a bare ".".
    """
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    prefix = "." * node.level
    if node.module:
        return [prefix + node.module]
    return [prefix + alias.name for alias in node.names]


def parse_python_edges(path: Path, rel: str) -> tuple[list[str], list[dict]]:
    """Imports and calls of one Python file, as (imports, calls).

    Same tolerance as `parse_python_file`: an unparseable file yields no edges instead
    of aborting the scan — one broken file must never cost the whole atlas.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return [], []

    imports: set[str] = set()
    # Keyed on the dedup key so the FIRST line seen wins: a name called in a loop or on
    # several branches is one edge, and the atlas stays small and byte-stable across rescans.
    calls: dict[tuple[str, str], int] = {}
    class_stack: list[str] = []
    func_stack: list[str] = []

    class Visitor(ast.NodeVisitor):
        def visit_Import(self, node: ast.Import) -> None:
            imports.update(_import_names(node))
            self.generic_visit(node)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            imports.update(_import_names(node))
            self.generic_visit(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            class_stack.append(node.name)
            self.generic_visit(node)
            class_stack.pop()

        def _visit_func(self, node) -> None:
            # Qualname built exactly like `parse_python_file` does (class stack only, no
            # enclosing-function prefix), so a `caller` is always literally an atlas symbol name.
            func_stack.append(".".join([*class_stack, node.name]))
            self.generic_visit(node)
            func_stack.pop()

        visit_FunctionDef = _visit_func
        visit_AsyncFunctionDef = _visit_func

        def visit_Call(self, node: ast.Call) -> None:
            callee = _callee_name(node.func)
            if callee is not None:
                caller = func_stack[-1] if func_stack else "<module>"
                calls.setdefault((caller, callee), node.lineno)
            self.generic_visit(node)

    Visitor().visit(tree)

    rows = [{"file": rel, "caller": caller, "callee": callee, "line": line}
            for (caller, callee), line in calls.items()]
    rows.sort(key=lambda row: (row["caller"], row["callee"]))
    return sorted(imports), rows


def assemble_edges(imports_by_file: dict[str, list[str]],
                   calls_by_file: dict[str, list[dict]]) -> dict:
    """Merge per-file results into the `atlas["edges"]` block, deterministically sorted.

    Sorting on the dedup key (file, caller, callee) — a total order — is what makes two
    scans of an unchanged tree produce byte-identical JSON, which agent loops rely on to
    diff atlases.
    """
    calls: list[dict] = [row for _, rows in sorted(calls_by_file.items()) for row in rows]
    calls.sort(key=lambda row: (row["file"], row["caller"], row["callee"]))
    return {
        "language": EDGES_LANGUAGE,
        "imports": {rel: imports_by_file[rel] for rel in sorted(imports_by_file)},
        "calls": calls,
    }


def previous_edges_by_file(previous: dict | None) -> tuple[dict[str, list[str]], dict[str, list[dict]]]:
    """Regroup a previous atlas's edges per file, so an unchanged file can be reused
    verbatim instead of re-parsed. Mirrors how `build_atlas` reuses symbols."""
    edges = (previous or {}).get("edges", {})
    imports_by_file = dict(edges.get("imports", {}))
    calls_by_file: dict[str, list[dict]] = {}
    for row in edges.get("calls", []):
        calls_by_file.setdefault(row["file"], []).append(row)
    return imports_by_file, calls_by_file


def callers_of(atlas: dict, name: str) -> list[dict]:
    """Edges whose callee matches `name` (compared against the last dotted segment of `name`)."""
    target = _last_segment(name)
    return [row for row in atlas.get("edges", {}).get("calls", []) if row["callee"] == target]


def callees_of(atlas: dict, name: str) -> list[dict]:
    """Edges whose caller matches `name` (exact qualname match, or last-segment match)."""
    target = _last_segment(name)
    return [row for row in atlas.get("edges", {}).get("calls", [])
            if row["caller"] == name or _last_segment(row["caller"]) == target]


def unreferenced_symbols(atlas: dict) -> list[dict]:
    """Python symbols whose name never appears as any callee anywhere in the atlas.

    A HINT, not a verdict. Static call edges cannot see dynamic dispatch, `getattr`,
    decorator registries, string-based plugin loading, entry points, or any call from a
    non-Python file — so a symbol listed here may well be live. Treat it as a place to
    look, never as proof of dead code.

    Dunders, `test_*` names and `main` are excluded outright: they are called by the
    interpreter, the test runner and the entry point respectively, so they would be
    permanent false positives.
    """
    referenced = {row["callee"] for row in atlas.get("edges", {}).get("calls", [])}

    out: list[dict] = []
    for section, rows in atlas.get("symbols", {}).items():
        for row in rows:
            if row.get("language") != EDGES_LANGUAGE:
                continue
            bare = _last_segment(row["name"])
            if bare.startswith("__") and bare.endswith("__"):
                continue
            if bare.startswith(_UNREFERENCED_EXCLUDED_PREFIX) or bare in _UNREFERENCED_EXCLUDED_EXACT:
                continue
            if bare in referenced:
                continue
            out.append({"section": section, "name": row["name"],
                        "file": row["file"], "line": row["line"]})

    out.sort(key=lambda row: (row["file"], row["line"]))
    return out
