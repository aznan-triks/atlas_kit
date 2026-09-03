"""Command-line entry point. Pure dispatch — no business logic lives here."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from code_fauna_codex import emit
from code_fauna_codex.diff import cmd_diff as _cmd_diff
from code_fauna_codex.doctor import cmd_doctor as _cmd_doctor
from code_fauna_codex.edges import callees_of, callers_of, unreferenced_symbols
from code_fauna_codex.index_store import codex_schema_error, load_json, save_json
from code_fauna_codex.scan import build_codex
from code_fauna_codex.semantic import (
    DEFAULT_BATCH_SIZE, DEFAULT_MIN_ZSCORE, DEFAULT_SIMILAR_MIN_ZSCORE, DEFAULT_TIMEOUT_S,
    DEFAULT_TOP_K, cmd_embed as _cmd_embed, cmd_search as _cmd_search,
    cmd_similar as _cmd_similar, cmd_status as _cmd_status,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NEEDS_USER = 2  # missing/invalid/exhausted API key, or an unusable codex/index —
                     # anything the user must arbitrate before the command can mean anything

VERSION_FALLBACK = "unknown (not installed — running from a source tree?)"

EPILOG = """\
exit codes:
  0  success (including "no results" — an empty answer is still an answer)
  1  a runtime failure the user cannot fix by passing a different flag
  2  user arbitration required: missing/invalid/exhausted API key, missing codex or
     index, or a file written by an incompatible schema version

network cost:
  free, offline, no API key:  scan  find  section  deps  unused  similar  status
                              diff  doctor
  one embedding API call:     embed (one per batch of --batch-size)  search (one)

examples:
  code-fauna-codex scan .                          build the codex
  code-fauna-codex find "cancel a job"             keyword search, offline
  code-fauna-codex deps build_codex                who calls it, what it calls (Python)
  code-fauna-codex unused --json | jq '.count'     dead-code hints, machine-readable
  code-fauna-codex doctor                          why is my setup not working?
"""


def _package_version() -> str:
    """The installed distribution's version. A source-tree run has no distribution
    metadata, which is not an error — report it as such rather than guessing."""
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version("code-fauna-codex")
    except PackageNotFoundError:
        return VERSION_FALLBACK


def _load_codex(path: Path, command: str, as_json: bool) -> dict | None:
    """Load a codex, or report why it cannot be trusted and return None.

    Fail Fast, shared by every mechanical reader: a missing codex is NOT an empty
    codex, and a codex from another schema is NOT silently half-read. Both used to
    surface as an empty result set with exit code 0, which reads as "nothing matches"
    when the truth is "nothing was even looked at".
    """
    if not path.exists():
        emit.fail(command, f"Codex not found: {path} — run `code-fauna-codex scan` first, "
                           f"or pass --codex <path>.", as_json)
        return None
    codex = load_json(path, {})
    schema_error = codex_schema_error(codex, path)
    if schema_error:
        emit.fail(command, schema_error, as_json)
        return None
    return codex


def cmd_scan(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    out_path = Path(args.out)
    previous = load_json(out_path, {})
    try:
        codex = build_codex(root, ignore_globs=args.ignore or [], previous=previous,
                            parser_mode=args.parser)
    except RuntimeError as exc:
        emit.fail("scan", str(exc), args.json)
        return EXIT_ERROR
    save_json(out_path, codex)

    total = sum(len(rows) for rows in codex["symbols"].values())
    edges = codex.get("edges") or {}
    if args.json:
        emit.json_ok("scan", root=str(root), out=str(out_path),
                     codex_schema_version=codex.get("schema_version"),
                     files=len(codex["files"]), symbols=total,
                     edges={"files_with_imports": len(edges.get("imports") or {}),
                            "calls": len(edges.get("calls") or [])})
    else:
        print(f"Indexed {len(codex['files'])} file(s), {total} symbol(s) -> {out_path}")
    return EXIT_OK


def cmd_find(args: argparse.Namespace) -> int:
    codex = _load_codex(Path(args.codex), "find", args.json)
    if codex is None:
        return EXIT_NEEDS_USER

    pattern = args.pattern.lower()
    hits = []
    for section, rows in codex.get("symbols", {}).items():
        for row in rows:
            haystack = " ".join([row.get("name", ""), row.get("signature", ""),
                                 row.get("docstring", ""), row.get("file", "")]).lower()
            if pattern in haystack:
                hits.append((section, row))

    if args.json:
        emit.json_ok("find", pattern=args.pattern, count=len(hits), results=[
            {"section": section, "name": row.get("name", ""), "file": row.get("file", ""),
             "line": row.get("line", 0), "signature": row.get("signature", ""),
             "docstring": row.get("docstring", "")}
            for section, row in hits
        ])
        return EXIT_OK

    if not hits:
        print(f"No resource matches '{args.pattern}'.")
        return EXIT_OK

    current = ""
    for section, row in hits:
        if section != current:
            current = section
            print(f"\n-- {section}")
        print(f"  {row['name']}  {row['file']}:{row['line']}  {row.get('signature', '')}")
    print(f"\n{len(hits)} result(s).")
    return EXIT_OK


def cmd_section(args: argparse.Namespace) -> int:
    codex = _load_codex(Path(args.codex), "section", args.json)
    if codex is None:
        return EXIT_NEEDS_USER

    rows = codex.get("symbols", {}).get(args.name, [])
    if args.json:
        emit.json_ok("section", name=args.name, count=len(rows), rows=rows)
    else:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    return EXIT_OK


def cmd_deps(args: argparse.Namespace) -> int:
    """Both directions of the call graph for one symbol. Python only — the edge data
    itself is Python-only, and saying so beats returning a confident empty list for a
    Go or TypeScript symbol."""
    codex = _load_codex(Path(args.codex), "deps", args.json)
    if codex is None:
        return EXIT_NEEDS_USER

    callers = callers_of(codex, args.symbol)
    callees = callees_of(codex, args.symbol)

    if args.json:
        emit.json_ok("deps", symbol=args.symbol, callers=callers, callees=callees,
                     caller_count=len(callers), callee_count=len(callees))
        return EXIT_OK

    print(f"-- callers of {args.symbol} ({len(callers)})")
    for edge in callers:
        print(f"  {edge['caller']}  {edge['file']}:{edge['line']}")
    print(f"\n-- called by {args.symbol} ({len(callees)})")
    for edge in callees:
        print(f"  {edge['callee']}  {edge['file']}:{edge['line']}")
    if not callers and not callees:
        print("\nNo edges. Note: call/import edges cover Python files only.")
    return EXIT_OK


def cmd_unused(args: argparse.Namespace) -> int:
    """Symbols never named at a call site. A HINT, not a verdict — see the warning
    printed with the report, and `unreferenced_symbols`' docstring."""
    codex = _load_codex(Path(args.codex), "unused", args.json)
    if codex is None:
        return EXIT_NEEDS_USER

    rows = unreferenced_symbols(codex)
    if args.json:
        emit.json_ok("unused", count=len(rows), symbols=rows,
                     caveat="Heuristic: a symbol reached only through dynamic dispatch, a "
                            "decorator, an entry point or getattr looks unreferenced here.")
        return EXIT_OK

    if not rows:
        print("No unreferenced Python symbol found.")
        return EXIT_OK
    for row in rows:
        print(f"  {row['section']}  {row['name']}  {row['file']}:{row['line']}")
    print(f"\n{len(rows)} unreferenced symbol(s) — HINT ONLY. Dynamic dispatch, decorators, "
          f"entry points and getattr all defeat this check. Verify before deleting.")
    return EXIT_OK


def cmd_diff(args: argparse.Namespace) -> int:
    return _cmd_diff(Path(args.old), Path(args.new), args.json)


def cmd_doctor(args: argparse.Namespace) -> int:
    return _cmd_doctor(Path(args.codex), Path(args.index), args.json)


def cmd_embed(args: argparse.Namespace) -> int:
    return _cmd_embed(Path(args.codex), Path(args.index), args.provider, args.model,
                      args.dimensions, args.batch_size, args.timeout, args.json)


def cmd_search(args: argparse.Namespace) -> int:
    return _cmd_search(args.question, Path(args.index), args.provider,
                       args.model, args.dimensions, args.top_k, args.min_score,
                       args.section, args.timeout, args.json)


def cmd_similar(args: argparse.Namespace) -> int:
    return _cmd_similar(Path(args.index), args.min_score, args.section,
                        args.exclude_same_file, args.json)


def cmd_status(args: argparse.Namespace) -> int:
    return _cmd_status(Path(args.codex), Path(args.index), args.json)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="code-fauna-codex", epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Mechanical + optional semantic code-symbol codex for any repository.",
    )
    parser.add_argument("--version", action="version",
                        version=f"code-fauna-codex {_package_version()}")
    # Carried by every subparser rather than the top-level parser, so `code-fauna-codex find x
    # --json` works — which is where a user (and an agent) naturally puts it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help=(
        "Machine-readable output: one JSON object on stdout, "
        "{command, schema_version, ok, ...}. Errors come back as ok:false with an "
        "\"error\" key on stdout too, so a caller only has to read one channel."
    ))
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", parents=[common],
                            help="Build the mechanical codex (no API key needed).")
    p_scan.add_argument("root", help="Repository root to scan.")
    p_scan.add_argument("--out", default="codex.json", help="Output JSON path.")
    p_scan.add_argument("--ignore", action="append", default=[], help=(
        "Glob to exclude (repeatable), matched against the POSIX-relative path. "
        "Unioned with the globs in a .codefaunacodexignore file at the scan root, if present."
    ))
    p_scan.add_argument("--parser", default="auto", choices=["auto", "regex", "treesitter"], help=(
        "Backend for .js/.jsx/.ts/.tsx, .go and .rs (Python is always ast). 'auto' "
        "(default) uses tree-sitter per extension when that language's grammar is "
        "installed, else regex. 'treesitter' forces it and fails loud, naming the pip "
        "package to install, if the grammar for a scanned extension is missing. 'regex' "
        "keeps the pre-tree-sitter best-effort behaviour."
    ))
    p_scan.set_defaults(func=cmd_scan)

    p_find = sub.add_parser("find", parents=[common], help="Search the codex by pattern.")
    p_find.add_argument("pattern")
    p_find.add_argument("--codex", default="codex.json")
    p_find.set_defaults(func=cmd_find)

    p_section = sub.add_parser("section", parents=[common],
                               help="Dump one codex section as JSON.")
    p_section.add_argument("name")
    p_section.add_argument("--codex", default="codex.json")
    p_section.set_defaults(func=cmd_section)

    p_deps = sub.add_parser("deps", parents=[common], help=(
        "Callers and callees of a symbol — offline, from the codex's call edges "
        "(Python files only)."
    ))
    p_deps.add_argument("symbol")
    p_deps.add_argument("--codex", default="codex.json")
    p_deps.set_defaults(func=cmd_deps)

    p_unused = sub.add_parser("unused", parents=[common], help=(
        "Python symbols never named at any call site — a HINT, not a verdict. Offline."
    ))
    p_unused.add_argument("--codex", default="codex.json")
    p_unused.set_defaults(func=cmd_unused)

    p_diff = sub.add_parser("diff", parents=[common], help=(
        "Compare two codex snapshots: added/removed/moved/re-signatured symbols. Offline."
    ))
    p_diff.add_argument("old", help="Path to the older codex.json.")
    p_diff.add_argument("new", help="Path to the newer codex.json.")
    p_diff.set_defaults(func=cmd_diff)

    p_doctor = sub.add_parser("doctor", parents=[common], help=(
        "Environment diagnostic: versions, parser backends, providers, keys configured "
        "(count only), codex/index state. Offline."
    ))
    p_doctor.add_argument("--codex", default="codex.json")
    p_doctor.add_argument("--index", default="semantic_index.json")
    p_doctor.set_defaults(func=cmd_doctor)

    p_embed = sub.add_parser("embed", parents=[common],
                             help="(Re)index the codex — needs an API key.")
    p_embed.add_argument("--codex", default="codex.json")
    p_embed.add_argument("--index", default="semantic_index.json")
    p_embed.add_argument("--provider", default="gemini", choices=["gemini", "openai", "local"])
    p_embed.add_argument("--model", default=None)
    p_embed.add_argument("--dimensions", type=int, default=None)
    p_embed.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    p_embed.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    p_embed.set_defaults(func=cmd_embed)

    p_search = sub.add_parser("search", parents=[common],
                              help="Search the codex by meaning — needs an API key.")
    p_search.add_argument("question")
    p_search.add_argument("--index", default="semantic_index.json")
    p_search.add_argument("--provider", default="gemini", choices=["gemini", "openai", "local"])
    p_search.add_argument("--model", default=None)
    p_search.add_argument("--dimensions", type=int, default=None)
    p_search.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    p_search.add_argument("--min-score", type=float, default=DEFAULT_MIN_ZSCORE, help=(
        "Relative z-score multiplier k (BREAKING CHANGE: no longer an absolute cosine "
        "cutoff). A candidate is kept only if its score >= mean + k*stdev of the full "
        "score distribution. Skipped entirely when the index has fewer than 5 entries."
    ))
    p_search.add_argument("--section", default=None)
    p_search.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    p_search.set_defaults(func=cmd_search)

    p_similar = sub.add_parser("similar", parents=[common], help=(
        "Find near-duplicate index entries — offline, no network call, no API key."
    ))
    p_similar.add_argument("--index", default="semantic_index.json")
    p_similar.add_argument("--min-score", type=float, default=DEFAULT_SIMILAR_MIN_ZSCORE, help=(
        "Relative z-score multiplier k for this command's own pair-population gate "
        "(separate default from `search`'s --min-score — near-duplicate detection wants "
        "precision over recall). A pair is kept only if its score >= mean + k*stdev of "
        "the full pair-score distribution. Skipped entirely when there are fewer than "
        "5 pairs."
    ))
    p_similar.add_argument("--section", default=None,
                           help="Only show pairs where both entries are in this section.")
    p_similar.add_argument("--exclude-same-file", action="store_true", default=False, help=(
        "Drop pairs whose two entries are in the same file. Same-file pairs are "
        "INCLUDED by default — this must be opted into explicitly."
    ))
    p_similar.set_defaults(func=cmd_similar)

    p_status = sub.add_parser("status", parents=[common],
                              help="Index state — offline, no network call.")
    p_status.add_argument("--codex", default="codex.json")
    p_status.add_argument("--index", default="semantic_index.json")
    p_status.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
