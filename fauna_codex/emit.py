"""Machine-readable output layer — the single place that knows the `--json` envelope.

Every command's JSON payload is one object on stdout:

    {"command": "<name>", "schema_version": <int>, "ok": <bool>, ...payload}

`ok: false` carries an "error" key instead of a payload. Consumers (agents, CI)
can therefore branch on two stable keys without parsing human text. Human output
stays exactly what it was — `--json` is purely additive.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Final

# Bumped only when the envelope or an existing command payload changes shape in a
# way that breaks an existing consumer. Adding a new key is not a break.
OUTPUT_SCHEMA_VERSION: Final = 1


def json_ok(command: str, **payload: Any) -> None:
    """Print one success envelope on stdout."""
    print(_dumps({"command": command, "schema_version": OUTPUT_SCHEMA_VERSION,
                  "ok": True, **payload}))


def json_error(command: str, message: str) -> None:
    """Print one failure envelope on stdout. The exit code still carries the verdict."""
    print(_dumps({"command": command, "schema_version": OUTPUT_SCHEMA_VERSION,
                  "ok": False, "error": message}))


def _dumps(envelope: dict) -> str:
    """`ensure_ascii=True` is deliberate and load-bearing, do not "fix" it.

    Human output is printed in the console's own encoding, which on Windows is a
    legacy codepage: an em dash goes out as cp1252 0x97, not as UTF-8. That is fine
    for a human terminal and wrong for a machine contract — a consumer decoding the
    pipe as UTF-8 would fail on the first non-ASCII character in a docstring or an
    error message. Escaping to \\uXXXX keeps the payload pure ASCII, which every
    codepage encodes identically and every JSON parser decodes back to the right
    characters.
    """
    return json.dumps(envelope, indent=2, ensure_ascii=True)


def fail(command: str, message: str, as_json: bool) -> None:
    """Report an error once, in whichever shape the caller asked for.

    JSON mode keeps stdout the only channel an agent must read; text mode keeps the
    pre-existing stderr behaviour byte-for-byte.
    """
    if as_json:
        json_error(command, message)
    else:
        print(message, file=sys.stderr)
