#!/usr/bin/env bash
# Pre-commit hook: flag near-duplicate symbols across files, using the semantic index.
#
# Install:
#     cp examples/pre-commit-similar.sh .git/hooks/pre-commit
#     chmod +x .git/hooks/pre-commit
#
# PREREQUISITE, read this first: `similar` reads an index that `atlas-kit embed` has to
# have built. `similar` itself is free and offline, but the hook is only useful if the
# index is reasonably fresh — so this script refreshes it, and THAT step costs embedding
# API calls (one per batch of 50 changed entries).
#
# Two ways to make that cost acceptable:
#   1. Use the on-device provider — no key, no network, no quota:
#          pip install 'atlas-kit[local]'
#          export ATLAS_PROVIDER=local
#   2. Set ATLAS_SKIP_EMBED=1 to never refresh here, and re-embed on your own schedule.
#      The hook then reports against a possibly stale index, which is fine for a
#      duplication smell test.

set -euo pipefail

PROVIDER="${ATLAS_PROVIDER:-local}"
THRESHOLD="${ATLAS_SIMILAR_MIN_SCORE:-2.0}"

command -v atlas-kit >/dev/null 2>&1 || {
    echo "atlas-kit not on PATH — skipping duplicate check." >&2
    exit 0
}

atlas-kit scan . >/dev/null

if [ "${ATLAS_SKIP_EMBED:-0}" != "1" ]; then
    if ! atlas-kit embed --provider "$PROVIDER" >/dev/null 2>&1; then
        echo "atlas: embed failed (no key, no quota, or provider unavailable) — " \
             "reporting against the existing index." >&2
    fi
fi

# --exclude-same-file: two helpers sitting in the same file are usually a deliberate
# pair, not an accident. Cross-file near-duplicates are the ones worth a second look.
OUT="$(atlas-kit similar --exclude-same-file --min-score "$THRESHOLD" --json 2>/dev/null || true)"

if [ -z "$OUT" ]; then
    echo "atlas: no semantic index yet — run 'atlas-kit embed' once to enable this check."
    exit 0
fi

if command -v jq >/dev/null 2>&1; then
    COUNT=$(echo "$OUT" | jq -r '.count // 0')
    if [ "$COUNT" -gt 0 ]; then
        echo "atlas: ${COUNT} cross-file near-duplicate pair(s) — review before committing:"
        echo "$OUT" | jq -r '.pairs[] | "  [\(.score | (.*100 | round) / 100)] \(.a.name) (\(.a.file)) <-> \(.b.name) (\(.b.file))"'
    else
        echo "atlas: no cross-file near-duplicates above threshold ${THRESHOLD}."
    fi
else
    atlas-kit similar --exclude-same-file --min-score "$THRESHOLD"
fi

# Reports, never blocks: near-duplicate is a judgement call, not a rule.
exit 0
