# Roadmap

This file is the public decision record for code-fauna-codex. Every suggestion that has been
put to the project is listed here with a verdict, so that a declined idea is not
re-proposed without new information, and a deferred one carries the trigger that would
revive it.

**Verdicts**

| Verdict | Meaning |
|---|---|
| **Shipped** | In the current release. |
| **Planned** | Accepted, scoped, not yet built. |
| **Deferred** | Not rejected on merit — waiting on the stated trigger. |
| **Declined** | Will not be built, for the stated reason. |

The two standards a suggestion is judged against are the project's own: **KISS/YAGNI**
(the simplest thing that works, no speculative code) and **mode 1 stays autonomous**
(the mechanical scan never needs an API key, a network call, or an optional dependency).
A feature that is genuinely useful to someone else's tool, but that would add a
dependency or a second way to do an existing thing here, is declined on purpose.

---

## What code-fauna-codex is for

An inventory of the symbols in a repository, answering one question fast and offline:
*does this already exist here?* Its primary consumer today is a coding agent that must
check before it writes. Every verdict below follows from that: things that make the
inventory more complete, more machine-readable, or more trustworthy win; things that
turn it into a linter, a graph database, a service, or a platform lose.

---

## Shipped

### Machine-readable contract (the agent-facing surface)

| # | Item | Note |
|---|---|---|
| 1 | `--json` on every command | One stable envelope: `{command, schema_version, ok, ...}`. Errors return `ok:false` with `error`, on stdout, so an agent reads one channel. |
| 2 | Stable per-command JSON schema | Documented in the README, versioned by `schema_version`. |
| 3 | `schema_version` in `codex.json` | Every reader validates it and fails fast. A future format change can no longer be silently misread. |
| 4 | Index-compatibility check on *every* command | `search`/`similar`/`status` previously read an index without checking its key schema; only `embed` did. All four now check. |
| 5 | Exit-code table | Documented in the README and in `--help`. |
| 6 | Which commands cost an API call | Documented explicitly: `scan`, `find`, `section`, `deps`, `unused`, `similar`, `status`, `diff`, `doctor` are free and offline; only `embed` and `search` hit the network. |
| 7 | Idempotent `scan` | Locked by a test: rescanning an unchanged tree produces a byte-identical `codex.json`. No git noise in an agent loop. |
| 8 | Documented Python API | `import code_fauna_codex` was already usable; it is now stated as supported, so callers stop shelling out. |

### Coverage of the code itself

| # | Item | Note |
|---|---|---|
| 9 | **Call and import edges** | The biggest gap: the codex had no relations. `codex.json` now carries `edges.imports` and `edges.calls`. Python only — extracted from `ast`, not guessed. |
| 10 | `code-fauna-codex deps <symbol>` | Callers and callees of a symbol. Offline. Gives the edge data a consumer, instead of shipping dead data. |
| 11 | `code-fauna-codex unused` | Symbols never named at any call site. Explicitly a **hint**, not a verdict — dynamic dispatch, decorators and entry points defeat it, and the docs say so. |
| 12 | Go and Rust via tree-sitter | They were stuck on the regex fallback while JS/TS had a real AST. Now the same backend, with per-grammar availability so a partially installed machine degrades predictably. |
| 13 | Per-language regression fixtures | Go and Rust get their own parsing tests. |
| 14 | `.codefaunacodexignore` | Union with `--ignore`, never a replacement. Excludes vendored and generated trees that are not in `.gitignore`. |

### Trust and diagnosis

| # | Item | Note |
|---|---|---|
| 15 | `code-fauna-codex doctor` | Offline diagnostic: version, Python, which parser backend each extension resolves to, which grammars are missing and their pip names, providers and whether a key is configured (**count only, never a value**), codex/index state. |
| 16 | `code-fauna-codex diff old.json new.json` | Added / removed / moved / re-signatured symbols between two snapshots. A report, not a gate — a CI script gates on the `--json` counts. |
| 17 | `--min-score` non-regression test | The flag's meaning changed (absolute cosine cutoff → z-score multiplier) and nothing guarded it. A test now makes a silent return to the old semantics impossible. |
| 18 | `--min-score` documented far more loudly | Including the answer to "are scores comparable across providers?" — see below. |
| 19 | `--version` | Was simply missing. |
| 20 | Richer `--help` | Examples and the exit-code table in the epilog. |
| 21 | Windows support stated | It is the development machine; it stops being an assumption and becomes a claim. |
| 22 | PyPI name checked | `code-fauna-codex` is free. (`codex` alone is taken, but that is not the name.) |
| 23 | Semver + deprecation policy | Stated in the README, prompted by the `--min-score` break having been discovered rather than announced. |
| 24 | `SECURITY.md`, `CONTRIBUTING.md`, this roadmap | Public repo hygiene. |
| 25 | Agent adoption kit | `SKILL.md` and `examples/` — a ready system-prompt fragment and a pre-commit script, so adoption does not require prompt engineering per user. |
| 26 | Positioning vs graph tools | A README section stating what this is not, so it is used as a complementary mechanical brick rather than compared to a knowledge-graph builder. |
| 27 | What `codex.json` contains | Documented: names, signatures, docstrings, paths — never file bodies or literal values. This is the honest answer to "should you scan for secrets before indexing". |
| 28 | Team-cache doctrine | Documented: `codex.json` is generated, gitignored by default, and why committing it is usually the wrong call. |
| 29 | Proxy support | Already worked (`requests` honours `HTTP_PROXY`/`HTTPS_PROXY`); now documented rather than reinvented. |

### Suggestions answered by an existing feature

Recorded so they are not rebuilt as a second way to do the same thing.

| Suggestion | Already covered by |
|---|---|
| Configurable provider timeout | `--timeout`, since before this pass. |
| `--dry-run` for `embed` (estimate calls first) | `code-fauna-codex status` reports exactly how many entries are not yet indexed — that *is* the estimate, offline. |
| `code-fauna-codex stats` | `status` + `doctor`. |
| `code-fauna-codex version --full` | `doctor`. |
| `code-fauna-codex explain <symbol>` | `find` + the new `deps`. |
| Scores comparable across providers | `--min-score` is a **z-score relative to the query's own score distribution**, not an absolute cosine. That is precisely what makes it provider-independent — an absolute threshold would not be. Documented instead of tested against paid providers. |
| `.env` separation | `.env.example` is versioned; `.env` never is. |

---

## Planned

| Item | Why it is not in this release |
|---|---|
| Mermaid / DOT export | Only became meaningful now that edges exist. Small, next pass. |
| Parser provenance per symbol (`ast` / `treesitter` / `regex`) | A consumer could then weigh a regex-derived symbol lower than an AST-derived one. Cheap, but it changes the `Symbol` record shape, which is better done in one deliberate schema bump. |
| Human-readable Markdown summary per module | Useful as a "read this before coding" artifact; competes with `--json` for effort, so it waits until someone reads it. |

---

## Deferred (with the trigger that revives them)

| Item | Trigger |
|---|---|
| Native MCP server | `--json` was the real prerequisite and it now exists, which makes an MCP wrapper thin. Deferred until an agent loop actually needs it, because the server itself would add a dependency and a runtime to a tool that currently has one dependency. |
| `similar` as a continuous CI drift scan | The pieces exist (`similar`, `--json`, the example hook). Deferred until a real repo runs it on a schedule and tells us what the report should say. |
| Size guardrails and a published benchmark | The project has an explicit rule against publishing a threshold it has not measured. Trigger: a scan on a reference repo (Django, React) is actually run and timed. `similar` is O(n²) in index entries — documented in the README rather than capped by a guessed limit. |
| Depth/pair cap for `similar` | Same measurement first. |
| `code-fauna-codex scan --path src/x` (partial scan) | `scan <root>` already accepts a subdirectory. The real feature is *merging* a partial scan into an existing codex, which needs a merge policy. Trigger: a monorepo user asks. |
| Monorepo/workspace auto-detection, per-package codexes, multi-repo federation | Same trigger. |
| `codefaunacodex.toml` central config | Flags plus `.codefaunacodexignore` cover today's knobs. Trigger: a third persistent setting appears. |
| Shell completion | Trigger: distribution beyond `pip install -e .`. |
| Per-symbol source hash | Would make dedup robust to line renumbering. Trigger: a real case where line drift caused a spurious re-embed. |
| `--stdin` (scan a diff/patch) | Trigger: a pre-commit hook that must not touch the working tree. |
| Retry with exponential backoff | Current doctrine is Fail Fast plus key rotation on 429. Trigger: transient non-429 failures observed in practice. |
| Additional providers (Bedrock, Vertex, Azure OpenAI) | The provider registry makes each a one-file addition. Trigger: someone with those credentials. |
| Git submodule traversal | Trigger: a repo that needs it. |
| Parser fuzzing | Trigger: a crash on malformed input (none observed; the parsers already swallow `SyntaxError`/`UnicodeDecodeError` per file). |
| CI: coverage badge, Dependabot/Renovate, GitHub Action, GitLab template | Trigger: a CI pipeline exists at all. All of these presuppose one. |
| Watch mode / push-diff to an agent | `scan` is incremental and hash-based; a shell loop covers it without a file-watching dependency. Trigger: measured scan latency that makes polling wasteful. |
| Codex history over time, branch-to-branch diff, auto-changelog from diffs | `diff` is the building block; all three are compositions of it plus git. Trigger: the composition proves annoying to do by hand. |

---

## Declined

Grouped by the reason, because the reasons repeat.

**It duplicates something that already exists here.**
`--format llm` (the human output is already one compact line per hit; `--json` covers machines — a third format is a third thing to keep in sync) · `--quiet` (`--json` is the quiet mode) · `--format table` (`find` output is already tabular) · CSV export (`--json` + `jq`) · `code-fauna-codex clean` (deleting two files) · `code-fauna-codex init` (there is no config file to scaffold) · command history log (the shell has one) · structured logging (a CLI with no daemon; `--json` is the machine channel) · synonym/alias search (that is what semantic search is for) · business-domain grouping (likewise).

**Nothing to disable / already true.**
`NO_COLOR` support (the CLI emits no ANSI colour at all) · `--no-network` strict mode (the offline commands are offline by construction and documented as such; a flag cannot make that more true) · typo suggestions on subcommands (argparse already prints the valid choices) · falsely-ignored binary files (the scanner works from an extension allowlist and never opens a binary).

**Wrong shape for what this tool produces.**
SARIF export (SARIF describes *findings*; a codex is an *inventory*) · cyclomatic complexity per symbol (that is `ruff`/`radon`'s job) · test-coverage cross-referencing · naming-convention recommendations · automatic refactoring from `similar` (the report is the suggestion; rewriting code is a different product) · mixed-language detection inside one file (the extension is the contract; JSX inside `.js` is already handled by the JS grammar).

**Costs more than it protects, at this threat model.**
Secret scanning before indexing (the codex stores names, signatures, docstrings and paths — never file bodies or literal values; documented rather than scanned) · cryptographic signing of the codex · SBOM publication · dependency licence scanning (one runtime dependency: `requests`) · sandboxing third-party parsers · checksumming downloaded local embedding models (that is `fastembed`'s contract) · hashing the tree-sitter grammar binaries (pinned versions in the extra are the standard answer) · embedding retention policy (a local file the user can delete) · a fuller key audit trail (rotation is already logged by key *position*, never by value) · a dedicated pre-commit hook for leaked API keys (that is `gitleaks`' job, and better done by it).

**Contradicts a design rule.**
Encrypted `.env` or OS keyring integration (environment variables are the boundary the OS already secures; a second secret path contradicts the single no-hardcode rule) · post-install auto-scan hooks (a package that scans your repo as an install side effect is a surprise, and surprises are the opposite of Fail Fast) · gzipped `codex.json` (it kills the diffability and greppability that are the point of a JSON inventory) · silent fallbacks of any kind.

**Not measurable as asked.**
Remaining-quota counter per API key. Embedding endpoints do not return remaining quota; the number would be invented. What is reported is the truth available: exhaustion, at the moment it happens, identified by key position.

**Premature distribution work for a project with no external users yet.**
Docker image (a pip-installable pure-Python CLI in a container is *more* setup, not less) · Homebrew/Scoop · npm mirror package · VS Code and JetBrains plugins · REST API service · Sourcegraph/OpenGrok export · Notion/Confluence connector · Slack/Discord webhooks · LangChain/LlamaIndex retriever · `pre-commit.com` framework config (the example script is shipped; the framework wiring waits for a user of it) · a two-letter `ak` alias. Every one of these becomes reasonable the day there are users to distribute to; none of them create those users.

**Already answered by the licence and the data flow.**
GDPR documentation. Nothing is collected or transmitted except, in mode 2 only and only when the user runs `embed`/`search`, the symbol text sent to the embedding provider the user themselves chose. One README line, not a compliance programme.
