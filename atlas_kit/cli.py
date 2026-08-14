"""Command-line entry point. Pure dispatch — no business logic lives here."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from atlas_kit.index_store import load_json, save_json
from atlas_kit.scan import build_atlas
from atlas_kit.semantic import (
    DEFAULT_BATCH_SIZE, DEFAULT_MIN_ZSCORE, DEFAULT_SIMILAR_MIN_ZSCORE, DEFAULT_TIMEOUT_S,
    DEFAULT_TOP_K, cmd_embed as _cmd_embed, cmd_search as _cmd_search,
    cmd_similar as _cmd_similar, cmd_status as _cmd_status,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NEEDS_USER = 2  # missing/invalid/exhausted API key — user arbitration required


def cmd_scan(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    out_path = Path(args.out)
    previous = load_json(out_path, {})
    atlas = build_atlas(root, ignore_globs=args.ignore or [], previous=previous)
    save_json(out_path, atlas)
    total = sum(len(rows) for rows in atlas["symbols"].values())
    print(f"Indexed {len(atlas['files'])} file(s), {total} symbol(s) -> {out_path}")
    return EXIT_OK


def cmd_find(args: argparse.Namespace) -> int:
    atlas = load_json(Path(args.atlas), {"symbols": {}})
    pattern = args.pattern.lower()
    hits = []
    for section, rows in atlas.get("symbols", {}).items():
        for row in rows:
            haystack = " ".join([row.get("name", ""), row.get("signature", ""),
                                 row.get("docstring", ""), row.get("file", "")]).lower()
            if pattern in haystack:
                hits.append((section, row))

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
    atlas = load_json(Path(args.atlas), {"symbols": {}})
    rows = atlas.get("symbols", {}).get(args.name, [])
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    return EXIT_OK


def cmd_embed(args: argparse.Namespace) -> int:
    return _cmd_embed(Path(args.atlas), Path(args.index), args.provider, args.model,
                      args.dimensions, args.batch_size, args.timeout)


def cmd_search(args: argparse.Namespace) -> int:
    return _cmd_search(args.question, Path(args.index), args.provider,
                       args.model, args.dimensions, args.top_k, args.min_score,
                       args.section, args.timeout)


def cmd_similar(args: argparse.Namespace) -> int:
    return _cmd_similar(Path(args.index), args.min_score, args.section, args.exclude_same_file)


def cmd_status(args: argparse.Namespace) -> int:
    return _cmd_status(Path(args.atlas), Path(args.index))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atlas-kit", description=(
        "Mechanical + optional semantic code-symbol atlas for any repository."
    ))
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="Build the mechanical atlas (no API key needed).")
    p_scan.add_argument("root", help="Repository root to scan.")
    p_scan.add_argument("--out", default="atlas.json", help="Output JSON path.")
    p_scan.add_argument("--ignore", action="append", default=[],
                        help="Glob to exclude (repeatable), matched against the POSIX-relative path.")
    p_scan.set_defaults(func=cmd_scan)

    p_find = sub.add_parser("find", help="Search the atlas by pattern.")
    p_find.add_argument("pattern")
    p_find.add_argument("--atlas", default="atlas.json")
    p_find.set_defaults(func=cmd_find)

    p_section = sub.add_parser("section", help="Dump one atlas section as JSON.")
    p_section.add_argument("name")
    p_section.add_argument("--atlas", default="atlas.json")
    p_section.set_defaults(func=cmd_section)

    p_embed = sub.add_parser("embed", help="(Re)index the atlas — needs an API key.")
    p_embed.add_argument("--atlas", default="atlas.json")
    p_embed.add_argument("--index", default="semantic_index.json")
    p_embed.add_argument("--provider", default="gemini", choices=["gemini", "openai"])
    p_embed.add_argument("--model", default=None)
    p_embed.add_argument("--dimensions", type=int, default=None)
    p_embed.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    p_embed.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    p_embed.set_defaults(func=cmd_embed)

    p_search = sub.add_parser("search", help="Search the atlas by meaning — needs an API key.")
    p_search.add_argument("question")
    p_search.add_argument("--index", default="semantic_index.json")
    p_search.add_argument("--provider", default="gemini", choices=["gemini", "openai"])
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

    p_similar = sub.add_parser("similar", help=(
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

    p_status = sub.add_parser("status", help="Index state — offline, no network call.")
    p_status.add_argument("--atlas", default="atlas.json")
    p_status.add_argument("--index", default="semantic_index.json")
    p_status.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
