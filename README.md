# code-fauna-codex

Mechanical + optional semantic code-symbol codex for any repository.

An inventory of every function, method and class in a repo — plus the call and import
edges between the Python ones — built to answer one question fast and offline:
**does this already exist here?**

## What it does

- **Mode 1 — mechanical (default, zero API key ever needed):** scans a repository and
  indexes its functions, methods and classes, plus Python call/import edges. Python is
  parsed via `ast`; JavaScript/TypeScript, Go and Rust via tree-sitter when the grammar
  is installed, with a documented best-effort regex fallback otherwise.
- **Mode 2 — semantic (optional):** turns the mechanical codex into a vector index and
  lets you search it by *meaning* instead of by keyword. The provider is pluggable —
  Gemini, OpenAI, or `local` which runs on-device with no key at all. Mode 1 works fully
  standalone; mode 2 is purely additive and never a prerequisite.

## Install

    pip install -e .                        # mode 1, one dependency
    pip install -e '.[treesitter]'          # real ASTs for JS/TS/Go/Rust
    pip install -e '.[local]'               # on-device embeddings, no API key
    pip install -e '.[dev]'                 # pytest

## Which mode do I need?

```
Do you know roughly what the thing is called?
├── yes ──────────────────────────► Mode 1.  code-fauna-codex find "<term>"
│                                   Offline, instant, no key. Start here, always.
└── no, only what it does
    │
    ├── Is your repo small enough to skim the codex?
    │   └── yes ──────────────────► Mode 1.  code-fauna-codex section python_functions
    │                               or `find` on a few guesses. Still free.
    │
    └── Genuinely need "find me whatever cancels a job, whatever it's called"?
        │
        ├── Can you install ~100 MB of on-device model?
        │   └── yes ──────────────► Mode 2, local provider. No key, no network, no quota.
        │                           pip install 'code-fauna-codex[local]'
        │                           code-fauna-codex embed --provider local
        │
        └── No, or you want stronger embeddings
            └── ────────────────────► Mode 2, Gemini or OpenAI. Costs API calls.
                                     export GEMINI_API_KEY=...
```

Short version: **mode 1 answers most questions**. Reach for mode 2 when the keyword you
would search for is exactly what you do not know. Nothing in mode 1 degrades if mode 2
is never set up.

## What each command costs

| Cost | Commands |
|---|---|
| **Free** — no network, no API key, no quota | `scan` `find` `section` `deps` `unused` `similar` `status` `diff` `doctor` |
| **One embedding API call** | `embed` (one per batch of `--batch-size`, default 50) · `search` (one per query) |

`similar` is free at query time but reads an index `embed` had to build first. The
`local` provider makes even `embed` and `search` network-free.

## Mode 1 — mechanical, no API key

    code-fauna-codex scan .                         # writes codex.json (incremental — only changed files are re-parsed)
    code-fauna-codex find "cancel a job"            # keyword search over names, signatures, docstrings, paths
    code-fauna-codex section python_functions       # dump one section as JSON
    code-fauna-codex deps build_codex               # callers and callees of a symbol (Python only)
    code-fauna-codex unused                         # symbols never named at a call site — a HINT, not a verdict
    code-fauna-codex diff old.json codex.json       # what changed between two snapshots
    code-fauna-codex doctor                         # environment diagnostic: why isn't this working?

### Call and import edges

`scan` records, for Python files only, which module each file imports and which names
each function calls. `codex.json` carries them under `edges`:

```json
"edges": {
  "language": "python",
  "imports": {"code_fauna_codex/scan.py": ["ast", "code_fauna_codex.edges", "..."]},
  "calls": [{"file": "code_fauna_codex/cli.py", "caller": "cmd_find", "callee": "load_json", "line": 91}]
}
```

Only the **last segment** of a call target is stored (`a.b.foo()` → `"foo"`), because
codex symbol names are qualnames whose last segment is the bare name — that is what
makes a match possible without full name resolution. Calls with no static name
(`f()()`, `d["k"]()`) are dropped rather than given a synthetic callee.

`code-fauna-codex unused` lists symbols whose name never appears as any callee. **It is a hint,
not a verdict**: dynamic dispatch, decorators, entry points and `getattr` all make a
live symbol look unreferenced. Dunders, `main` and `test_*` are excluded already.
Verify before deleting anything.

### Excluding files — `.codefaunacodexignore`

Put one glob per line in `.codefaunacodexignore` at the scan root (`#` comments and blank
lines are skipped). Each glob is matched exactly like an `--ignore` argument, against
the POSIX-relative path:

    generated/**
    **/*_pb2.py
    vendor/**

`.codefaunacodexignore` is always a **union** with `--ignore`, never a replacement: a
repo-wide file cannot silently re-include what a caller excluded on the command line.
A set of common directories (`.git`, `node_modules`, `__pycache__`, `dist`, `build`,
`target`, `vendor`, `.venv`, `.claude`, …) is skipped unconditionally.

## Mode 2 — semantic

| Provider | Environment variable | Default model | API key |
|----------|-----------------------|----------------|---------|
| gemini   | `GEMINI_API_KEY`      | `gemini-embedding-001`   | required |
| openai   | `OPENAI_API_KEY`      | `text-embedding-3-small` | required |
| local    | —                     | `BAAI/bge-small-en-v1.5` | none — on-device via `fastembed`, `pip install 'code-fauna-codex[local]'` |

    export GEMINI_API_KEY=...
    code-fauna-codex embed --provider gemini        # incremental — only new/changed entries cost a call
    code-fauna-codex search "cancel a running job" --provider gemini
    code-fauna-codex similar                        # near-duplicate report — offline, no key, no quota
    code-fauna-codex status                         # offline — index state

Missing or invalid key, or quota exceeded: the command exits non-zero with a message
naming the problem. **There is never a silent fallback** to another provider or to
mode 1.

**Planning the cost of an `embed` run:** `code-fauna-codex status` reports how many codex
entries are not yet indexed. That number, divided by `--batch-size`, is the number of
API calls the next `embed` will make. It is computed offline — no dry-run flag needed.

**Multi-key rotation** — set `GEMINI_API_KEYS` / `OPENAI_API_KEYS` (plural,
comma-separated) instead of the singular variable to give `embed`/`search` a pool. On
`QuotaExhausted` (HTTP 429) the next key is tried, printed to stderr as `Key 1/2
exhausted …` — **by position, never by value**. An exhausted key is dropped for the rest
of the run rather than retried on every batch. An invalid key is never rotated past:
that is a config error, not a capacity one. The singular variable still works as a
one-key pool.

Behind a corporate proxy, `requests` honours `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` —
nothing extra to configure.

### `--min-score` — read this before scripting it

**BREAKING (0.2.0): `--min-score` on `search` changed meaning.** It used to be an
absolute cosine cutoff (e.g. `0.55`). It is now a **z-score multiplier k**, default
`1.0`: a result is kept only if its score ≥ mean + k·stdev of that query's full score
distribution. A saved script still passing an old-style value will behave very
differently — re-tune it. `similar` has its own, stricter default (`2.0`), because
near-duplicate detection wants precision over recall.

Why relative and not absolute: measured on this corpus, two *unrelated* symbols share
a raw cosine of ~0.7 to 0.97 before recentring — the shared-domain bias of any code
corpus. An absolute threshold therefore discriminates nothing. A z-score does.

This is also the answer to *"are scores comparable between the `local`, Gemini and
OpenAI providers?"* — **yes, by construction.** The threshold is relative to each
query's own distribution within one index, so it does not depend on a provider's
absolute cosine scale. An absolute cutoff would have needed per-provider tuning.

`tests/test_min_score_semantics.py` exists solely to make a silent return to absolute
cutoff semantics impossible.

The first `embed` after upgrading auto-migrates the index's internal key format
(one-time, no API call) and prunes entries for symbols no longer in the codex.

## Machine-readable output — `--json`

Every command accepts `--json` and prints exactly one object on stdout:

```json
{"command": "find", "schema_version": 1, "ok": true, "count": 2, "results": [ ... ]}
```

Failures use the same envelope — `"ok": false` with an `"error"` string, **also on
stdout** — so a caller only ever reads one channel. The exit code still carries the
verdict.

| Command | Payload keys |
|---|---|
| `scan` | `root` `out` `codex_schema_version` `files` `symbols` `edges{files_with_imports,calls}` |
| `find` | `pattern` `count` `results[{section,name,file,line,signature,docstring}]` |
| `section` | `name` `count` `rows[]` |
| `deps` | `symbol` `callers[]` `callees[]` `caller_count` `callee_count` |
| `unused` | `count` `symbols[{section,name,file,line}]` `caveat` |
| `diff` | `old` `new` `summary{files_added,files_removed,files_changed,symbols_added,symbols_removed,symbols_moved,symbols_signature_changed}` `files{}` `symbols{}` |
| `doctor` | `runtime` `parsers` `providers[]` `files` |
| `embed` | `codex` `index` `provider` `model` `dim` `entries_total` `entries_indexed` `pruned` `migrated` |
| `search` | `question` `count` `threshold_applied` `results[{score,section,name,file,line,signature,docstring}]` |
| `similar` | `count` `entries` `pairs_considered` `same_file_pairs` `excluded_same_file` `pairs[{score,a,b}]` |
| `status` | `codex{path,resources,schema_version}` `index{path,resources,model,dim,key_schema,stale}` |

`schema_version` in the envelope is the *output* contract version. It is bumped only
when an existing key changes shape; adding a key is not a break.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success — including "no results". An empty answer is still an answer. |
| `1` | A runtime failure the user cannot fix by passing a different flag. |
| `2` | User arbitration required: missing/invalid/exhausted API key, missing codex or index, or a file written by an incompatible schema version. |

A `2` always names its own fix — usually `code-fauna-codex scan` or `code-fauna-codex embed`. When it
does not, `code-fauna-codex doctor` will.

## Parsing, per language

| Language | Backend | Extracted |
|---|---|---|
| Python | `ast`, always, built in | functions, methods, classes, **imports, call edges** |
| JavaScript / TypeScript | tree-sitter (optional) or regex | functions, classes, class methods, `const x = () => …` arrow functions |
| Go | tree-sitter (optional) or regex | `func` declarations, receiver methods (named `Type.Method`), `struct` types |
| Rust | tree-sitter (optional) or regex | `fn` items, `impl` methods (named `Type.method`), `struct`, `enum` |

    pip install 'code-fauna-codex[treesitter]'
    code-fauna-codex scan . --parser treesitter

`--parser auto` (the default) picks tree-sitter **per extension** when that language's
grammar is importable, and regex otherwise — a machine with the JS grammar but not the
Go one behaves predictably rather than all-or-nothing. `--parser treesitter` forces it
and **fails loud, naming the pip package to install**, if a grammar is missing: never a
silent downgrade to regex. `--parser regex` keeps the pre-tree-sitter behaviour.
`code-fauna-codex doctor` reports which backend each extension actually resolves to.

The regex backend is honest best-effort line matching, not a parser: good enough to
answer "does this exist", not a complete parse. Docstrings are only extracted for
Python.

## The `codex.json` file

```json
{
  "root": "...", "schema_version": 1,
  "files": {"path/to/file.py": "<sha256>"},
  "symbols": {"python_functions": [{"name": ..., "file": ..., "line": ..., "signature": ..., "docstring": ..., "language": ...}]},
  "edges": {"language": "python", "imports": {...}, "calls": [...]}
}
```

**What it contains, and does not.** Symbol names, signatures, docstrings, file paths and
file hashes. It never stores file bodies, string literals, or any *value* from your
source — which is why code-fauna-codex does not scan for secrets before indexing: a secret in a
variable's value cannot reach the codex. A secret in a *docstring* or a *function name*
would, so treat `codex.json` with the same care as the source it describes.

**`schema_version` is checked by every reader**, which fails fast rather than
half-reading a file from another version. Rebuild with `code-fauna-codex scan`.

**`scan` is idempotent**: rescanning an unchanged tree produces a byte-identical
`codex.json`, edges included. This is locked by a test, because an agent loop that
rescans on every turn must not generate git noise.

**Commit it, or generate it?** Generate it. `codex.json` is gitignored by default: it
is derived data, it changes on every commit that touches code, and a stale committed
copy is worse than no copy — it answers "does this exist" with yesterday's truth. `scan`
is incremental and fast enough to run on demand. Commit it only if you have a consumer
that cannot run `scan` itself, and then refresh it in CI, not by hand.

## Using it from Python

The CLI is a thin dispatch layer; every command's logic is an importable function with
no `argparse` in the signature. Shelling out is not required:

```python
from pathlib import Path
from code_fauna_codex.scan import build_codex
from code_fauna_codex.edges import callers_of, unreferenced_symbols
from code_fauna_codex.diff import diff_codexes

codex = build_codex(Path("."))
callers_of(codex, "process_payment")
unreferenced_symbols(codex)
diff_codexes(old_codex, codex).summary()
```

`build_codex`, the `edges` query helpers and `diff_codexes` are pure and I/O-free (apart
from `build_codex` reading the tree it is given) — no network, no global state.

## Positioning — what this is not

code-fauna-codex is a **mechanical brick**, deliberately narrow:

- **Not a knowledge graph.** It records call and import edges as flat facts; it does no
  community detection, no clustering, no embedding of the graph structure. Tools that
  build a real graph can consume `codex.json` — the format is documented and versioned
  for exactly that — rather than compete with it.
- **Not a linter.** No complexity scores, no style rules, no findings. `unused` is a
  hint, and says so.
- **Not a service.** One CLI, one JSON file, one runtime dependency (`requests`).

Its differentiator is that mode 1 needs **no API key, no network, no optional
dependency, and no setup beyond `pip install`**. That is a design constraint, not a
current limitation.

## Adding a provider

Implement `code_fauna_codex.providers.base.EmbeddingProvider` (one `embed(request, http_post)`
method) in a new file under `code_fauna_codex/providers/`, then register it in
`code_fauna_codex/providers/__init__.py::PROVIDERS`. `http_post` is always injectable — see
`tests/test_providers.py` for the pattern that keeps the test suite network-free. Set
`requires_api_key = False` for a provider that needs no key (see `providers/local.py`).

## Adding a parser backend

Implement `code_fauna_codex.parsers.base.CodeParser` (`name`, `extensions`, `available`, one
`parse(path, rel)` method) in a new file under `code_fauna_codex/parsers/`, then register it in
`code_fauna_codex/parsers/__init__.py::PARSERS`. A backend whose coverage varies per extension
also implements `supports(extension)`.

## Performance

`scan` is incremental: unchanged files are never re-parsed, and their symbols and edges
are reused verbatim from the previous codex.

`similar` is **O(n²) in indexed entries** — it compares every pair. That is fine for the
thousands of symbols a normal repo has, and will not be for a very large monorepo. No
cap is imposed, because no threshold has been measured on a reference repository yet;
publishing a guessed limit would be worse than publishing none. See `ROADMAP.md`.

## Versioning and deprecation

Semantic versioning. Breaking changes are announced in `CHANGELOG.md`.

A flag whose *meaning* changes while its *name* stays the same is the worst kind of
break — it fails silently in existing scripts. That happened once, to `--min-score`
(absolute cutoff → z-score multiplier), and it is documented loudly above and pinned by
a regression test. The policy that follows from it:

1. A rename gets a deprecation period: the old name keeps working and warns.
2. A **semantic** change to an existing flag does not get one — it gets a new flag name,
   or a major version. Silence is the failure mode being avoided.
3. On-disk formats are versioned (`schema_version` in the codex, `key_schema` in the
   index) and every reader validates before trusting.

## Non-goals

- No per-language parser beyond Python (`ast`), and tree-sitter/regex for JS/TS/Go/Rust.
- No call/import edges outside Python — a regex backend cannot tell a call from a
  mention, and a confident wrong edge is worse than none.
- Not a linter, a graph database, or a service. See "Positioning" above.

## Contributing

See `CONTRIBUTING.md` for the rules the code follows, `ROADMAP.md` for the verdict on
every suggestion the project has received (including the declined ones and why), and
`SECURITY.md` for how keys and your code are handled.

## License

MIT — see `LICENSE`.
