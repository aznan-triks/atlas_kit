# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [0.3.0] - 2026-09-02

Theme: make the atlas readable by a machine, and make it cover the relations it was
missing. Every suggestion behind this release — accepted, deferred or declined — is
recorded with its reasoning in the new `ROADMAP.md`.

### Added
- **Call and import edges for Python.** `atlas.json` now carries an `edges` block:
  which modules each file imports, and which names each function calls. Extracted from
  `ast`, never guessed. Python only — a regex backend cannot tell a call from a mention,
  and a confident wrong edge is worse than none.
- **`atlas-kit deps <symbol>`** — callers and callees of a symbol, offline.
- **`atlas-kit unused`** — symbols never named at any call site. Explicitly a hint, not
  a verdict: dynamic dispatch, decorators, entry points and `getattr` defeat it.
- **`atlas-kit diff <old.json> <new.json>`** — added / removed / moved / re-signatured
  symbols and changed files between two snapshots. A report, not a gate; CI gates on the
  `--json` counts.
- **`atlas-kit doctor`** — offline diagnostic: versions, which parser backend each
  extension resolves to, which tree-sitter grammars are missing and their pip names,
  which providers have a key configured (count only, never a value), atlas and index
  state.
- **`--json` on every command.** One envelope, `{command, schema_version, ok, ...}`, on
  stdout. Errors use the same envelope with `ok: false` and an `error` key, also on
  stdout, so a caller reads one channel. The per-command payload schema is documented in
  the README.
- **Go and Rust via tree-sitter.** They were stuck on the regex fallback while JS/TS had
  a real AST. Go gets `func` declarations, receiver methods (named `Type.Method`) and
  `struct` types; Rust gets `fn` items, `impl` methods (named `Type.method`), `struct`
  and `enum`. New extras: `tree-sitter-go`, `tree-sitter-rust`.
- **Per-grammar parser availability.** `--parser auto` now resolves per extension: a
  machine with the JS grammar but not the Go one degrades predictably instead of
  all-or-nothing. `--parser treesitter` on an extension whose grammar is missing fails
  loud, naming the exact pip package — never a silent downgrade to regex.
- **`.atlaskitignore`** — one glob per line at the scan root, always a union with
  `--ignore`, never a replacement.
- **`schema_version` in `atlas.json`**, validated by every reader before the file is
  trusted.
- **`--version`**, and a `--help` epilog carrying the exit-code table, the network-cost
  table, and examples.
- `SKILL.md`, `examples/agent-system-prompt.md`, `examples/pre-commit-atlas.sh` and
  `examples/pre-commit-similar.sh` — an adoption kit so using atlas-kit from a coding
  agent does not require per-user prompt engineering.
- `ROADMAP.md`, `CONTRIBUTING.md`, `SECURITY.md`.

### Changed
- **`find` and `section` now fail fast (exit 2) on a missing or schema-incompatible
  atlas**, instead of returning an empty result set with exit code 0. This is the same
  bug class fixed in 0.2.1 for `embed`/`status`: "nothing matches" and "nothing was even
  looked at" must not be the same answer. A script that relied on the silent-empty
  behaviour will now see exit code 2 — run `atlas-kit scan` first.
- **`search`, `similar` and `status` now verify the index's `key_schema`** before
  reading it, and exit 2 asking for `atlas-kit embed` if it is stale. Previously only
  `embed` checked, so the other three could silently mismatch keys against an index
  written by an older schema.
- README rewritten: mode 1 vs mode 2 decision tree, per-command network cost, JSON
  payload schemas, exit codes, per-language parsing table, what `atlas.json` does and
  does not contain, the commit-it-or-generate-it doctrine, the Python API, positioning,
  and a stated versioning and deprecation policy.

### Fixed
- Nothing new — 0.3.0 is additive over 0.2.1. Two guards were added (above) for bug
  classes that had not yet produced a report but were reachable by the same route as the
  0.2.1 incident.

### Notes
- `scan` idempotence is now locked by a test: rescanning an unchanged tree produces a
  byte-identical `atlas.json`, edges included, so an agent loop that rescans every turn
  generates no git noise.
- `--min-score`'s z-score semantics are now pinned by `tests/test_min_score_semantics.py`,
  which exists to make a silent return to the old absolute-cutoff behaviour impossible.
- The name `atlas-kit` was verified to be free on PyPI.

## [0.2.1] - 2026-09-02

### Fixed
- `embed`/`status` no longer silently treat a missing `--atlas` file as an empty atlas. A missing atlas path now fails fast (exit code 2) before any index comparison or prune.
- `cmd_embed` no longer silently resets an incompatible index (model/dimensions mismatch) without telling the user — a stderr message now announces the reset and the number of vectors dropped.
- Multi-key API rotation is now "sticky" across embedding batches: an exhausted key is dropped for good instead of being retried (and re-failing) on every subsequent batch.

## [0.2.0] - previous session

### Added
- API key rotation for embedding providers.
- Local embedding provider (offline, no API key required).
- Tree-sitter based parser for JS/TS symbol extraction.

### Fixed
- Semantic-index key collision: `entry_key` now includes the line number, preventing two same-named symbols in one file from overwriting each other in the index.
- Background-similarity noise reduced.
