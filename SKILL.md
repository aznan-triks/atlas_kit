---
name: fauna-codex
description: Check whether a symbol already exists in this repository before writing new code. Use before implementing any function, class or method; when asked "does X already exist", "where is X used", "what calls X"; when looking for duplicate or near-duplicate implementations; or when a change needs the list of a symbol's callers. Mechanical commands are offline and cost nothing.
---

# fauna-codex

An inventory of every function, method and class in a repository, plus the call and
import edges between the Python ones. It answers **"does this already exist here?"**
without reading the whole codebase.

## The rule

**Query the atlas before writing a new symbol.** The failure mode this prevents is
writing a second implementation of something that already exists three directories
away under a different name.

## Cost — read this before choosing a command

| Cost | Commands |
|---|---|
| **Free.** No network, no API key, no quota. Call these as often as useful. | `scan` `find` `section` `deps` `unused` `similar` `status` `diff` `doctor` |
| **One embedding API call.** Needs a key. Ask before spending quota if you are not sure. | `embed` (one call per batch of 50) · `search` (one call per query) |

`similar` is free *at query time* but reads an index that `embed` had to build first.

## Workflow

```bash
fauna-codex scan .                    # build/refresh atlas.json — incremental, only re-parses changed files
fauna-codex find "cancel"             # keyword match over names, signatures, docstrings, paths
fauna-codex deps process_payment      # who calls it, what it calls (Python only)
fauna-codex unused                    # symbols never named at a call site — a HINT, verify before deleting
```

Then, only if a keyword search was not enough and a key is available:

```bash
fauna-codex embed --provider local    # 'local' runs on-device, no key: pip install 'fauna-codex[local]'
fauna-codex search "cancel a running job"
fauna-codex similar --exclude-same-file   # near-duplicate report over the whole index
```

## Machine-readable output

Add `--json` to any command. You get exactly one object on stdout:

```json
{"command": "find", "schema_version": 1, "ok": true, "count": 2, "results": [...]}
```

Errors come back the same way — `"ok": false` with an `"error"` string, also on stdout —
so you only ever have to read one channel. The exit code still carries the verdict.

## Exit codes

| Code | Meaning | What to do |
|---|---|---|
| `0` | Success, including "no results". An empty answer is an answer. | Continue. |
| `1` | Runtime failure. | Report it; a different flag will not fix it. |
| `2` | User arbitration needed: missing/invalid/exhausted API key, missing atlas or index, incompatible schema. | Read the `error` message — it names the exact fix (usually `fauna-codex scan` or `fauna-codex embed`). |

`fauna-codex doctor` explains a `2` when the message is not enough: it reports versions,
which parser backend each file extension resolves to, which providers have a key
configured (count only, never a value), and the state of the atlas and index files.

## What it does not do

- **Edges are Python-only.** `deps` and `unused` return nothing meaningful for
  JavaScript, TypeScript, Go or Rust. Their symbols are still indexed and findable.
- **`unused` is a heuristic.** Dynamic dispatch, decorators, entry points and `getattr`
  all make a live symbol look unreferenced. Never delete on its word alone.
- **It is an inventory, not a linter.** No complexity scores, no style rules, no
  findings. For those, use the repository's actual linter.
- **Non-Python languages without a tree-sitter grammar installed** fall back to
  best-effort regex line matching: good enough to answer "does this exist", not a
  complete parse. `doctor` says which backend is actually in use.
