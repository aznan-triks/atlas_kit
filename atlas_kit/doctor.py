"""`doctor` — offline self-diagnostic: why might an atlas-kit command fail here?

Answers, without a single network call or API request: which atlas-kit is running,
which parser backend each supported extension would really use in `auto` mode, which
embedding providers have a key configured, and whether the atlas/index files on disk
are readable and current.

SECURITY: an API key value is never read into the report, never formatted, never
printed. Only `keys_configured: <int>` — presence and count — leaves this module.

Exit code is 0 whenever the diagnostic itself completed, even when everything it
found is missing or misconfigured: a diagnostic REPORTS, it does not gate. 1 is
reserved for the diagnostic itself blowing up.
"""
from __future__ import annotations

import json
import platform
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from atlas_kit import emit
from atlas_kit.index_store import atlas_schema_error
from atlas_kit.parsers import PARSERS, resolve_parser
from atlas_kit.parsers.base import supports_extension
# Private, and deliberately so: it is the single source of truth for "which pip package
# ships which extension's grammar". Re-deriving that table here would be a second,
# divergent copy — the exact failure mode this diagnostic exists to catch.
from atlas_kit.parsers.treesitter_parser import _PIP_PACKAGE_BY_EXT
from atlas_kit.providers import PROVIDERS
from atlas_kit.scan import SUPPORTED_EXTENSIONS
# Imported, not reimplemented: the plural-then-singular env-var rule must stay ONE
# rule, or doctor would cheerfully report a key that `embed` cannot find.
from atlas_kit.semantic import _resolve_api_keys

COMMAND = "doctor"

# Python has exactly one engine (`ast`, in scan.py) and no parser-registry entry, so
# the registry cannot name its backend for `.py`. Named here instead of silently
# reporting "none" for the language the scanner parses best.
_PYTHON_BACKEND = "ast (built-in)"

_TREESITTER_HINT = "pip install 'atlas-kit[treesitter]'"


@dataclass
class RuntimeInfo:
    atlas_kit_version: str
    python_version: str
    platform: str


@dataclass
class GrammarInfo:
    package: str            # pip name, e.g. "tree-sitter-go"
    extensions: list[str]   # what this one package unlocks
    installed: bool         # importable AND usable right now


@dataclass
class ParsersInfo:
    mode: str
    backend_by_extension: dict[str, str]
    treesitter_available: bool
    grammars: list[GrammarInfo]
    install_hint: str | None


@dataclass
class ProviderInfo:
    name: str
    default_model: str
    default_dimensions: int
    requires_api_key: bool
    env_var: str | None
    keys_configured: int


@dataclass
class AtlasInfo:
    path: str
    exists: bool
    size_bytes: int
    read_error: str | None
    schema_version: int | None
    schema_error: str | None
    file_count: int | None
    symbol_count: int | None


@dataclass
class IndexInfo:
    path: str
    exists: bool
    size_bytes: int
    read_error: str | None
    entry_count: int | None
    model: str | None
    dim: int | None
    key_schema: int | None
    has_centroid: bool


@dataclass
class FilesInfo:
    atlas: AtlasInfo
    index: IndexInfo


@dataclass
class DoctorReport:
    runtime: RuntimeInfo
    parsers: ParsersInfo
    providers: list[ProviderInfo]
    files: FilesInfo


def _read_json(path: Path) -> tuple[dict | None, str | None]:
    """Return (data, error). Unlike `index_store.load_json`, a malformed or unreadable
    file is REPORTED instead of silently replaced by a default — a broken atlas is
    exactly the kind of failure cause a user runs `doctor` to find.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return None, f"unreadable: {exc.strerror or exc}"
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"
    if not isinstance(data, dict):
        return None, f"unexpected top-level {type(data).__name__}, expected an object"
    return data, None


def _size_bytes(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _collect_runtime() -> RuntimeInfo:
    try:
        installed = version("atlas-kit")
    except PackageNotFoundError:
        # Running from a source checkout without `pip install -e .` — a real and
        # common state, not an error.
        installed = "unknown (not installed)"
    return RuntimeInfo(atlas_kit_version=installed,
                       python_version=platform.python_version(),
                       platform=platform.platform())


def _collect_parsers() -> ParsersInfo:
    """Which backend `auto` mode would really pick, extension by extension.

    Reported per extension rather than per backend because tree-sitter ships one pip
    package per grammar: .go can be AST-parsed on this machine while .rs falls back to
    regex, and only a per-extension answer explains that.
    """
    treesitter = PARSERS["treesitter"]
    backends: dict[str, str] = {}
    for extension in sorted(SUPPORTED_EXTENSIONS):
        if extension == ".py":
            backends[extension] = _PYTHON_BACKEND
            continue
        parser = resolve_parser(extension, "auto")  # never raises in auto mode
        backends[extension] = parser.name if parser else "none"

    by_package: dict[str, list[str]] = {}
    for extension, package in sorted(_PIP_PACKAGE_BY_EXT.items()):
        by_package.setdefault(package, []).append(extension)
    grammars = [
        GrammarInfo(package=package, extensions=extensions,
                    installed=all(supports_extension(treesitter, e) for e in extensions))
        for package, extensions in sorted(by_package.items())
    ]
    missing = [g for g in grammars if not g.installed]
    return ParsersInfo(mode="auto", backend_by_extension=backends,
                       treesitter_available=treesitter.available, grammars=grammars,
                       install_hint=_TREESITTER_HINT if missing else None)


def _collect_providers() -> list[ProviderInfo]:
    out: list[ProviderInfo] = []
    for name in sorted(PROVIDERS):
        provider = PROVIDERS[name]
        # Only the COUNT crosses this line. The list itself is dropped immediately.
        keys = len(_resolve_api_keys(provider)) if provider.requires_api_key else 0
        out.append(ProviderInfo(
            name=provider.name,
            default_model=provider.default_model,
            default_dimensions=provider.default_dimensions,
            requires_api_key=provider.requires_api_key,
            env_var=provider.env_var or None,
            keys_configured=keys,
        ))
    return out


def _collect_atlas(path: Path) -> AtlasInfo:
    info = AtlasInfo(path=str(path), exists=path.exists(), size_bytes=0, read_error=None,
                     schema_version=None, schema_error=None, file_count=None,
                     symbol_count=None)
    if not info.exists:
        return info
    info.size_bytes = _size_bytes(path)
    atlas, error = _read_json(path)
    if atlas is None:
        info.read_error = error
        return info
    info.schema_version = int(atlas.get("schema_version") or 0)
    info.schema_error = atlas_schema_error(atlas, path)
    info.file_count = len(atlas.get("files") or {})
    info.symbol_count = sum(len(rows) for rows in (atlas.get("symbols") or {}).values())
    return info


def _collect_index(path: Path) -> IndexInfo:
    info = IndexInfo(path=str(path), exists=path.exists(), size_bytes=0, read_error=None,
                     entry_count=None, model=None, dim=None, key_schema=None,
                     has_centroid=False)
    if not info.exists:
        return info
    info.size_bytes = _size_bytes(path)
    index, error = _read_json(path)
    if index is None:
        info.read_error = error
        return info
    info.entry_count = len(index.get("entries") or {})
    info.model = index.get("model") or None
    info.dim = int(index.get("dim") or 0)
    key_schema = index.get("key_schema")
    info.key_schema = int(key_schema) if key_schema is not None else None
    info.has_centroid = bool(index.get("centroid"))
    return info


def _collect(atlas_path: Path, index_path: Path) -> DoctorReport:
    return DoctorReport(
        runtime=_collect_runtime(),
        parsers=_collect_parsers(),
        providers=_collect_providers(),
        files=FilesInfo(atlas=_collect_atlas(atlas_path), index=_collect_index(index_path)),
    )


def _print_human(report: DoctorReport) -> None:
    print("atlas-kit doctor — offline diagnostic, no network call, no API call.")

    runtime = report.runtime
    print("\n-- runtime")
    print(f"  atlas-kit : {runtime.atlas_kit_version}")
    print(f"  Python    : {runtime.python_version}")
    print(f"  platform  : {runtime.platform}")

    parsers = report.parsers
    print(f"\n-- parser backends (mode: {parsers.mode})")
    for extension, backend in parsers.backend_by_extension.items():
        print(f"  {extension:<5} -> {backend}")
    print("  tree-sitter grammars:")
    for grammar in parsers.grammars:
        covers = ", ".join(grammar.extensions)
        state = "installed" if grammar.installed else f"MISSING — pip install {grammar.package}"
        print(f"    {grammar.package:<24} ({covers}): {state}")
    if parsers.install_hint:
        print(f"    all at once: {parsers.install_hint}")

    print("\n-- embedding providers")
    for provider in report.providers:
        head = (f"  {provider.name:<7} model {provider.default_model} / "
                f"{provider.default_dimensions} dim")
        if not provider.requires_api_key:
            print(f"{head} — no API key needed")
            continue
        state = (f"{provider.keys_configured} key(s) configured"
                 if provider.keys_configured else "no key configured")
        print(f"{head} — {provider.env_var}: {state}")
    print("  (key values are never read or printed — count only; set the plural "
          "<VAR>S, comma-separated, for rotation)")

    atlas = report.files.atlas
    print("\n-- files")
    if not atlas.exists:
        print(f"  atlas {atlas.path}: MISSING — run `atlas-kit scan`.")
    elif atlas.read_error:
        print(f"  atlas {atlas.path}: {atlas.read_error} — re-run `atlas-kit scan`.")
    else:
        print(f"  atlas {atlas.path}: {atlas.size_bytes} bytes, schema "
              f"{atlas.schema_version}, {atlas.file_count} file(s), "
              f"{atlas.symbol_count} symbol(s)")
        if atlas.schema_error:
            print(f"    schema: {atlas.schema_error}")

    index = report.files.index
    if not index.exists:
        print(f"  index {index.path}: MISSING — run `atlas-kit embed` to create it.")
    elif index.read_error:
        print(f"  index {index.path}: {index.read_error} — re-run `atlas-kit embed`.")
    else:
        print(f"  index {index.path}: {index.size_bytes} bytes, {index.entry_count} "
              f"entrie(s), model {index.model or '(none)'} / {index.dim or 0} dim, "
              f"key_schema {index.key_schema if index.key_schema is not None else '(none)'}")
        if not index.has_centroid:
            print("    centroid: absent — `search`/`similar` need `atlas-kit embed` to rebuild.")
        else:
            print("    centroid: present")


def cmd_doctor(atlas_path: Path, index_path: Path, as_json: bool = False) -> int:
    """Report, offline, everything needed to explain why a command might fail here.

    Returns 0 whenever the diagnostic itself completed — even when the atlas is
    missing, no API key is set, or tree-sitter is not installed. A diagnostic reports,
    it does not gate; the commands themselves still refuse what they cannot do.
    Returns 1 only if the diagnostic itself blew up.
    """
    try:
        report = _collect(atlas_path, index_path)
    except Exception as exc:  # noqa: BLE001 — a diagnostic must never mask its own failure
        emit.fail(COMMAND, f"doctor failed to collect diagnostics: {exc}", as_json)
        return 1

    if as_json:
        emit.json_ok(COMMAND, **asdict(report))
    else:
        _print_human(report)
    return 0
