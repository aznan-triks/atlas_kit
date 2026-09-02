# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [0.2.1] - 2026-09-02

### Fixed
- `embed`/`status` no longer silently treat a missing `--atlas` file as an empty atlas. A missing atlas path now fails fast (exit code 2) before any index comparison or prune.
- `cmd_embed` no longer silently resets an incompatible index (model/dimensions mismatch) without telling the user — a stderr message now announces the reset and the number of vectors dropped.
- Multi-key API rotation is now "sticky" across embedding batches: an exhausted key is dropped for good instead of being retried (and re-failing) on every subsequent batch.

## [0.2.0] - previous session

### Added
- API key rotation for embedding providers.
- Local embedding provider (offline, no API key required).
- Tree-sitter based parser for JS/TS symbol extraction.

### Fixed
- Semantic-index key collision: `entry_key` now includes the line number, preventing two same-named symbols in one file from overwriting each other in the index.
- Background-similarity noise reduced.
