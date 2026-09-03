# Security

## Reporting a vulnerability

Open a private security advisory on the GitHub repository
(`Security` → `Report a vulnerability`). Please do not open a public issue for a
vulnerability report.

Expect an acknowledgement; this is a solo-maintained project, so a fix lands when it
lands. If a report is declined, you will get the reason.

## What fauna-codex handles

**API keys.** Only from environment variables — `GEMINI_API_KEY`, `OPENAI_API_KEY`, or
their plural `..._KEYS` forms for rotation. Keys are never written to `atlas.json`, to
the semantic index, or to any log. When a key is exhausted, the rotation message names
it by **position** (`Key 1/2 exhausted`), never by value. `fauna-codex doctor` reports how
many keys are configured, never a key itself — there is a test that asserts a key value
cannot reach stdout.

`.env` is never read by fauna-codex. Only `.env.example` is versioned.

**Your code.** In mode 1 (`scan`, `find`, `section`, `deps`, `unused`, `diff`, `doctor`)
nothing leaves the machine — there is no network call at all.

`atlas.json` stores symbol names, signatures, docstrings, file paths and file hashes. It
does not store file bodies, string literals, or any value from your source. That is why
fauna-codex does not scan for secrets before indexing: a secret in a variable's *value*
never enters the atlas. A secret in a *docstring* or a *function name* would, so treat
`atlas.json` with the same care as the source it describes — by default it is
gitignored.

**Mode 2.** `embed` and `search` are the only commands that make a network call. They
send the text of your symbols — name, signature, docstring, path — to the embedding
provider you selected with `--provider`. Nothing else is transmitted, and nothing is
transmitted at all unless you run one of those two commands. The `local` provider
(`pip install 'fauna-codex[local]'`) does the same work on-device with no network call.

## Supported versions

The latest release only. See `CHANGELOG.md`.
