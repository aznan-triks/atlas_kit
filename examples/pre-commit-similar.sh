#!/usr/bin/env bash
# Pre-commit hook: flag near-duplicate symbols across files, using the semantic index.
#
# Install:
#     cp examples/pre-commit-similar.sh .git/hooks/pre-commit
#     chmod +x .git/hooks/pre-commit
#
# PREREQUISITE, read this first: `similar` reads an index that `code-fauna-codex embed` has to
# have built. `similar` itself is free and offline, but the hook is only useful if the
# index is reasonably fresh — so this script refreshes it, and THAT step costs embedding
# API calls (one per batch of 50 changed entries).
#
# Two ways to make that cost acceptable:
#   1. Use the on-device provider — no key, no network, no quota:
#          pip install 'code-fauna-codex[local]'
#          export CODEX_PROVIDER=local
#   2. Set CODEX_SKIP_EMBED=1 to never refresh here, and re-embed on your own schedule.
#      The hook then reports against a possibly stale index, which is fine for a
#      duplication smell test.

set -euo pipefail

PROVIDER="${CODEX_PROVIDER:-local}"
THRESHOLD="${CODEX_SIMILAR_MIN_SCORE:-2.0}"

command -v code-fauna-codex >/dev/null 2>&1 || {
    echo "code-fauna-codex not on PATH — skipping duplicate check." >&2
    exit 0
}

code-fauna-codex scan . >/dev/null

if [ "${CODEX_SKIP_EMBED:-0}" != "1" ]; then
    if ! code-fauna-codex embed --provider "$PROVIDER" >/dev/null 2>&1; then
        echo "codex: embed failed (no key, no quota, or provider unavailable) — " \
             "reporting against the existing index." >&2
    fi
fi

# --exclude-same-file: two helpers sitting in the same file are usually a deliberate
# pair, not an accident. Cross-file near-duplicates are the ones worth a second look.
OUT="$(code-fauna-codex similar --exclude-same-file --min-score "$THRESHOLD" --json 2>/dev/null || true)"

if [ -z "$OUT" ]; then
    echo "codex: no semantic index yet — run 'code-fauna-codex embed' once to enable this check."
    exit 0
fi

if command -v jq >/dev/null 2>&1; then
    COUNT=$(echo "$OUT" | jq -r '.count // 0')
    if [ "$COUNT" -gt 0 ]; then
        echo "codex: ${COUNT} cross-file near-duplicate pair(s) — review before committing:"
        echo "$OUT" | jq -r '.pairs[] | "  [\(.score | (.*100 | round) / 100)] \(.a.name) (\(.a.file)) <-> \(.b.name) (\(.b.file))"'
    else
        echo "codex: no cross-file near-duplicates above threshold ${THRESHOLD}."
    fi
else
    code-fauna-codex similar --exclude-same-file --min-score "$THRESHOLD"
fi

# Reports, never blocks: near-duplicate is a judgement call, not a rule.
exit 0
