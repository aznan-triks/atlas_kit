# Example: running atlas-kit against AEVO3

atlas-kit has no knowledge of AEVO3 — this is only a usage illustration.

    cd /path/to/AEVO3
    pip install -e tools/atlas_kit
    atlas-kit scan . --out /tmp/aevo3_atlas.json --ignore "dashboard-vue/dist/*" --ignore ".venv-aevo3/*"
    atlas-kit find "cancel" --atlas /tmp/aevo3_atlas.json

For semantic search, export a key and run `atlas-kit embed` / `atlas-kit search` the
same way as on any other repository — AEVO3 itself uses a separate, purpose-built tool
(`scripts/atlas_semantic.py`) wired to its own `PROJECT_ATLAS.json` and `settings.yaml`;
atlas-kit is the generic, extractable version of the same idea.
