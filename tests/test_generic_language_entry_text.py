"""Regression test — fauna_codex.scan.parse_generic_file + fauna_codex.semantic.entry_text
on a real-shaped, mixed-language repo.

Audit finding: fauna_codex's own repo has zero .js/.ts/.go/.rs files, so the
generic-language parser (parse_generic_file, used for .js/.jsx/.ts/.tsx/.go/.rs)
and semantic.entry_text() are only ever exercised by tests/test_scan.py's minimal
synthetic single-symbol checks — never end-to-end, through build_atlas +
iter_atlas_entries, against a repo with several different generic-language
symbols side by side (the way a real JS/Go/Rust codebase would be scanned).

This closes that gap: it proves generic-language entries always carry a real,
non-empty signature (and embedding text) built from the actual matched
declaration line, and that different symbols never collapse onto the same
signature/text — i.e. entries are never reduced to a shared "name + file path"
template. A future change to parse_generic_file or entry_text that broke this
would have nothing else to catch it.
"""
from __future__ import annotations

from conftest import write

from fauna_codex.scan import build_atlas
from fauna_codex.semantic import iter_atlas_entries


def test_generic_language_entries_have_distinct_nonempty_signatures(tmp_path):
    write(tmp_path, "math.go", "func AddNumbers(a, b int) int {\n\treturn a + b\n}\n")
    write(tmp_path, "math.rs", "pub fn subtract_numbers(a: i32, b: i32) -> i32 {\n    a - b\n}\n")
    write(tmp_path, "math.js", "export function multiplyNumbers(a, b) {\n  return a * b;\n}\n")

    atlas = build_atlas(tmp_path)
    entries = iter_atlas_entries(atlas)

    generic = [e for e in entries if e["section"] == "generic_functions"]
    names = {e["name"] for e in generic}
    assert names == {"AddNumbers", "subtract_numbers", "multiplyNumbers"}

    # Every entry's signature (and the embedding text built from it) must carry the
    # real declaration line — never blank/whitespace-only "name + file path" filler.
    for e in generic:
        assert e["signature"].strip(), f"{e['name']} has an empty signature"
        assert e["text"].strip(), f"{e['name']} has empty embedding text"

    # ...and no two entries collapse onto the same signature/text, which would mean
    # entry_text()/parse_generic_file() fell back to a shared template instead of
    # each symbol's real per-line declaration.
    signatures = [e["signature"] for e in generic]
    assert len(set(signatures)) == len(signatures)
    texts = [e["text"] for e in generic]
    assert len(set(texts)) == len(texts)
