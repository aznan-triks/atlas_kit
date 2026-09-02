#!/usr/bin/env bash
# Pre-commit hook: refresh the atlas and report what the commit adds.
#
# Install:
#     cp examples/pre-commit-atlas.sh .git/hooks/pre-commit
#     chmod +x .git/hooks/pre-commit
#
# Cost: zero. Every command used here is offline — no network call, no API key, no
# quota. The hook is therefore safe to run on every commit, including offline.
#
# It never blocks a commit by default. Set ATLAS_STRICT=1 to make it fail when the
# commit removes a symbol (useful on a branch that is supposed to be additive).

set -euo pipefail

command -v atlas-kit >/dev/null 2>&1 || {
    echo "atlas-kit not on PATH — skipping atlas check." >&2
    exit 0
}

SNAPSHOT="$(mktemp -t atlas-pre-commit-XXXXXX.json)"
trap 'rm -f "$SNAPSHOT"' EXIT

# Keep the previous atlas so we can diff against it. A first run has none, which is
# not an error — there is simply nothing to compare yet.
if [ -f atlas.json ]; then
    cp atlas.json "$SNAPSHOT"
fi

atlas-kit scan . >/dev/null

if [ ! -s "$SNAPSHOT" ]; then
    echo "atlas: first scan, nothing to compare."
    exit 0
fi

DIFF_JSON="$(atlas-kit diff "$SNAPSHOT" atlas.json --json)"

# jq is optional: without it, just show the human report and stop.
if ! command -v jq >/dev/null 2>&1; then
    atlas-kit diff "$SNAPSHOT" atlas.json
    exit 0
fi

ADDED=$(echo "$DIFF_JSON" | jq '.summary.symbols_added')
REMOVED=$(echo "$DIFF_JSON" | jq '.summary.symbols_removed')
echo "atlas: +${ADDED} symbol(s), -${REMOVED} symbol(s) in this commit."

if [ "$ADDED" -gt 0 ]; then
    echo "New symbols — confirm none of these duplicate something that already exists:"
    echo "$DIFF_JSON" | jq -r '.symbols.added[] | "  \(.name)  \(.file):\(.line)"'
fi

if [ "${ATLAS_STRICT:-0}" = "1" ] && [ "$REMOVED" -gt 0 ]; then
    echo "ATLAS_STRICT=1 and this commit removes ${REMOVED} symbol(s) — refusing." >&2
    exit 1
fi

exit 0
