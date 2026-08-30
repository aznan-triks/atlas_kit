# atlas-kit

Mechanical + optional semantic code-symbol atlas for any repository.

## What it does

- **Mode 1 — mechanical (default, zero API key ever needed):** scans a repository
  and indexes its functions, methods and classes. Python is parsed precisely via
  `ast`; JavaScript/TypeScript, Go and Rust get a best-effort regex fallback
  (documented as non-exhaustive — good enough to answer "does this already exist",
  not a full per-language parser).
- **Mode 2 — semantic (optional, needs one API key):** turns the mechanical atlas
  into a vector index and lets you search it by *meaning* instead of by keyword.
  Provider is pluggable — pick Gemini or OpenAI via `--provider` / an environment
  variable. Mode 1 works fully standalone; mode 2 is purely additive.

## Install

    pip install -e .

## Mode 1 — mechanical, no API key

    atlas-kit scan .                         # writes atlas.json
    atlas-kit find "cancel a job"            # keyword search
    atlas-kit section python_functions       # dump one section as JSON

Python is always parsed via `ast`. JS/JSX/TS/TSX, Go and Rust go through
`--parser {auto,regex,treesitter}` (default `auto`) — see "Parsing JS/TS" below.

## Mode 2 — semantic, one API key (or none, with `local`)

Pick a provider and export its key:

| Provider | Environment variable | Default model              | API key   |
|----------|-----------------------|-----------------------------|-----------|
| gemini   | `GEMINI_API_KEY`      | `gemini-embedding-001`      | required  |
| openai   | `OPENAI_API_KEY`      | `text-embedding-3-small`    | required  |
| local    | —                     | `BAAI/bge-small-en-v1.5`    | none — runs on-device via `fastembed`, `pip install 'atlas-kit[local]'` |

    export GEMINI_API_KEY=...
    atlas-kit embed --provider gemini        # incremental — only new/changed entries cost a call
    atlas-kit search "cancel a running job" --provider gemini
    atlas-kit similar                        # near-duplicate report — offline, no API key
    atlas-kit status                         # offline — index state, no network call

Missing or invalid key, or quota exceeded: the command exits non-zero with a message
naming the problem. There is never a silent fallback to another provider or to mode 1.

**Multi-key rotation** — set `GEMINI_API_KEYS` / `OPENAI_API_KEYS` (plural, comma-separated)
instead of the singular var to give `embed`/`search` a pool of keys. On `QuotaExhausted`
(HTTP 429) the next key is tried automatically — printed to stderr as `Key 1/2 exhausted
...` (position only, never the key value); an invalid key is never rotated past (Fail
Fast — that's a config error, not a capacity one). The singular var still works as a
one-key pool.

**`atlas-kit similar`** finds near-duplicate entries already sitting in the index —
all-pairs cosine similarity, entirely offline (no network call, no API key, zero
quota cost). Run it after `embed` to spot redundant symbols. `--section` restricts
pairs to one section; `--exclude-same-file` drops same-file pairs (included by
default).

**BREAKING: `--min-score` on `search` changed meaning.** It used to be an absolute
cosine cutoff (e.g. `0.55`). It is now a z-score multiplier k, default `1.0`: a
result is kept only if its score >= mean + k*stdev of that query's full score
distribution. A saved script still passing the old-style value (e.g.
`--min-score 0.55`) will behave very differently now — re-tune it. `similar` has
its own, stricter `--min-score` default (`2.0`).

The first `atlas-kit embed` run after upgrading auto-migrates the index's internal
key format (one-time, printed to stdout, no extra API call/quota cost) and starts
pruning entries for symbols no longer in the atlas.

## Parsing JS/TS — `--parser auto|regex|treesitter`

    pip install 'atlas-kit[treesitter]'
    atlas-kit scan . --parser treesitter     # or omit --parser: auto picks it up when installed

`treesitter` gives a real AST for `.js`/`.jsx`/`.ts`/`.tsx` instead of regex_parser's
best-effort line matching — it also extracts class methods and `const x = () => ...`
arrow functions, which the regex backend never did. `--parser` only affects those four
extensions: Python stays `ast`, Go/Rust stay regex (no second backend exists for them
yet). `treesitter` forces the tree-sitter backend and fails loud (non-zero exit) if the
extra isn't installed — never a silent fallback to regex. `regex` keeps the old default
behaviour. `auto` (the default) uses tree-sitter when installed, else regex.

## Adding a provider

Implement `atlas_kit.providers.base.EmbeddingProvider` (one `embed(request, http_post)`
method) in a new file under `atlas_kit/providers/`, then register it in
`atlas_kit/providers/__init__.py::PROVIDERS`. `http_post` is always injectable — see
`tests/test_providers.py` for the pattern used to keep the test suite network-free.
Set `requires_api_key = False` for a provider that needs no key (see `providers/local.py`).

## Adding a parser backend

Implement `atlas_kit.parsers.base.CodeParser` (`name`, `extensions`, `available`, one
`parse(path, rel)` method) in a new file under `atlas_kit/parsers/`, then register it in
`atlas_kit/parsers/__init__.py::PARSERS` and extend `resolve_parser()`'s mode handling.

## Non-goals

- No per-language parser beyond Python (`ast`), JS/TS (tree-sitter, optional) and the
  regex-based fallback for JS/TS/Go/Rust above.

## License

MIT — see `LICENSE`.
