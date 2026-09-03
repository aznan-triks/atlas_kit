# Example: running fauna-codex against AEVO3

fauna-codex has no knowledge of AEVO3 — this is only a usage illustration.

    cd /path/to/AEVO3
    pip install -e tools/fauna_codex
    fauna-codex scan . --out /tmp/aevo3_atlas.json --ignore "dashboard-vue/dist/*" --ignore ".venv-aevo3/*"
    fauna-codex find "cancel" --atlas /tmp/aevo3_atlas.json

For semantic search, export a key and run `fauna-codex embed` / `fauna-codex search` the
same way as on any other repository — AEVO3 itself uses a separate, purpose-built tool
(`scripts/atlas_semantic.py`) wired to its own `PROJECT_ATLAS.json` and `settings.yaml`;
fauna-codex is the generic, extractable version of the same idea.
