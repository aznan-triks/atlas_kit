# Drop-in system-prompt fragment

Paste this into a coding agent's system prompt (Claude Code, Codex, Cursor, or your own
harness) so it consults the atlas before writing code. It assumes `atlas-kit` is on the
PATH and `atlas.json` is at the repository root.

---

## Copy from here

```
## Before writing code: check the atlas

This repository has an atlas of its symbols. Query it before implementing any new
function, class or method — writing a second implementation of something that already
exists is the failure this prevents.

    atlas-kit scan .                     # refresh (incremental; only changed files are re-parsed)
    atlas-kit find "<term>" --json       # keyword search over names, signatures, docstrings, paths
    atlas-kit deps <symbol> --json       # callers and callees (Python only)
    atlas-kit unused --json              # never-called symbols — a hint, not a verdict

These commands are offline: no network call, no API key, no quota. Call them freely.
Only `embed` and `search` cost an API call; ask before running those.

Every command accepts `--json` and answers with one object on stdout:
{"command": ..., "schema_version": 1, "ok": true|false, ...}. On failure, "ok" is false
and "error" holds the message — read stdout, not stderr.

Exit codes: 0 = success (including zero results), 1 = runtime failure,
2 = you must act (missing atlas or index, missing/invalid API key, incompatible schema).
On a 2, read the error message: it names the fix, usually `atlas-kit scan` or
`atlas-kit embed`. If it is still unclear, run `atlas-kit doctor`.

Report what you found before you write. If `find` returns a symbol that already covers
the need, extend it rather than adding a sibling. If nothing matches, say so explicitly
— that is the evidence that a new symbol is justified.
```

## To here

---

## Tightening it

- **If the agent must not spend quota**, delete the sentence about `embed`/`search` and
  keep only the offline commands. Mode 1 is fully self-sufficient.
- **If the repository is not Python**, delete the `deps` and `unused` lines: call edges
  are extracted from Python's `ast` and do not exist for other languages.
- **If the agent has no shell access**, `import atlas_kit` exposes the same functions —
  `scan.build_atlas`, `edges.callers_of`, `edges.unreferenced_symbols`,
  `diff.diff_atlases` — with no subprocess in between.
