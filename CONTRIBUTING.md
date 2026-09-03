# Contributing

## Before you write code

Read `ROADMAP.md` first. It records a verdict for every suggestion the project has
received, including the declined ones and why. If your idea is listed as *Declined*,
the pull request will need to argue against the stated reason — which is a fine thing
to do, with new information.

## The rules the code follows

These are not style preferences; a change that breaks one of them will be asked to
change, however good it otherwise is.

1. **KISS and YAGNI.** The simplest thing that works. No speculative abstraction, no
   option nobody asked for.
2. **Mode 1 stays autonomous.** The mechanical scan (`scan`, `find`, `section`, `deps`,
   `unused`, `diff`, `doctor`) must never require an API key, a network call, or an
   optional dependency. This is the project's differentiator, not an implementation
   detail.
3. **Fail Fast, never a silent fallback.** A missing key, an unreadable index, a codex
   from an incompatible schema: say so, exit non-zero. Never quietly switch provider,
   never quietly degrade to mechanical mode, never treat a missing file as an empty
   one. The project has shipped that last bug once — a missing codex read as empty,
   which pruned an entire index while reporting success.
4. **No hardcoded secrets.** Keys come from environment variables. Always.
5. **DRY.** One implementation of a rule. If two commands need the same check, it is
   one helper called twice.
6. **Root cause, not symptom.** Do not adjust a threshold or a timeout before
   explaining why the underlying call fails.
7. **Layer isolation.** `scan.py` never imports `semantic.py` or `providers/`. A new
   embedding provider is one file in `providers/` plus one line in the registry — never
   a branch somewhere else. Same for a parser backend in `parsers/`.

## Adding a provider or a parser backend

Both are registries. See the "Adding a provider" and "Adding a parser backend" sections
of the README — each is one new file plus one registry entry, no changes anywhere else.
`http_post` is always injectable in a provider, which is what keeps the test suite
network-free.

## Tests

    pip install -e ".[dev]"
    python -m pytest tests/ -q

The whole suite is offline: no test makes a real network call, and no test needs an API
key. Keep it that way — fake the provider, do not skip the test. Use `tmp_path` for
anything touching the filesystem.

A bug fix comes with a test that fails before the fix. Several test files exist purely
to pin a past bug in place (`test_semantic_centroid_bug.py`,
`test_semantic_dedup_bug.py`, `test_min_score_semantics.py`); do not delete one because
it looks redundant — read what it documents first.

## Language

Code, comments, docstrings, CLI output, commit messages and pull requests are in
English. Comments explain *why*, not *what* — several comments in `semantic.py` are
load-bearing project memory about decisions that are not obvious from the code.

## Versioning

Semantic versioning. Breaking changes are announced in `CHANGELOG.md` and given a
deprecation period where one is possible — see the README's versioning section.
