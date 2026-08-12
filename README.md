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

## Mode 2 — semantic, one API key

Pick a provider and export its key:

| Provider | Environment variable | Default model            |
|----------|-----------------------|---------------------------|
| gemini   | `GEMINI_API_KEY`      | `gemini-embedding-001`    |
| openai   | `OPENAI_API_KEY`      | `text-embedding-3-small`  |

    export GEMINI_API_KEY=...
    atlas-kit embed --provider gemini        # incremental — only new/changed entries cost a call
    atlas-kit search "cancel a running job" --provider gemini
    atlas-kit status                         # offline — index state, no network call

Missing or invalid key, or quota exceeded: the command exits non-zero with a message
naming the problem. There is never a silent fallback to another provider or to mode 1.

## Adding a provider

Implement `atlas_kit.providers.base.EmbeddingProvider` (one `embed(request, http_post)`
method) in a new file under `atlas_kit/providers/`, then register it in
`atlas_kit/providers/__init__.py::PROVIDERS`. `http_post` is always injectable — see
`tests/test_providers.py` for the pattern used to keep the test suite network-free.

## Non-goals (v1)

- No multi-key rotation / cooldown — a rate-limited key simply fails the command.
- No local/offline embedding provider — one runtime dependency (`requests`) only.
- No per-language parser beyond Python (AST) and the four regex-based languages above.

## License

MIT — see `LICENSE`.
