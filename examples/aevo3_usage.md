# Example: running code-fauna-codex against AEVO3

code-fauna-codex has no knowledge of AEVO3 — this is only a usage illustration.

    cd /path/to/AEVO3
    pip install -e tools/code_fauna_codex
    code-fauna-codex scan . --out /tmp/aevo3_codex.json --ignore "dashboard-vue/dist/*" --ignore ".venv-aevo3/*"
    code-fauna-codex find "cancel" --codex /tmp/aevo3_codex.json

For semantic search, export a key and run `code-fauna-codex embed` / `code-fauna-codex search` the
same way as on any other repository — AEVO3 itself uses a separate, purpose-built tool
(`scripts/codex_semantic.py`) wired to its own `PROJECT_CODEX.json` and `settings.yaml`;
code-fauna-codex is the generic, extractable version of the same idea.
