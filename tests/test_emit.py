"""The `--json` envelope contract. These tests exist because every command's
machine-readable output is only as trustworthy as this one module: a consumer branches
on `ok` and `command`, and decodes the stream as UTF-8 or ASCII.
"""
from __future__ import annotations

import json

import pytest

from atlas_kit import emit


def test_success_envelope_carries_command_schema_and_ok(capsys: pytest.CaptureFixture[str]) -> None:
    emit.json_ok("find", count=2, results=[{"name": "alpha"}])
    payload = json.loads(capsys.readouterr().out)

    assert payload["command"] == "find"
    assert payload["schema_version"] == emit.OUTPUT_SCHEMA_VERSION
    assert payload["ok"] is True
    assert payload["count"] == 2
    assert payload["results"] == [{"name": "alpha"}]


def test_error_envelope_is_ok_false_with_error_on_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    emit.json_error("embed", "Atlas not found: atlas.json")
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["ok"] is False
    assert payload["error"] == "Atlas not found: atlas.json"
    # An agent must only have to read one channel.
    assert captured.err == ""


def test_fail_routes_to_stdout_json_or_stderr_text(capsys: pytest.CaptureFixture[str]) -> None:
    emit.fail("status", "boom", as_json=True)
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"] == "boom"
    assert captured.err == ""

    emit.fail("status", "boom", as_json=False)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "boom"


@pytest.mark.parametrize("text", [
    "unrelated — em dash",       # what every error message in this project uses
    "café naïve",           # accented latin
    "日本語",             # non-latin script
])
def test_json_payload_is_pure_ascii(text: str, capsys: pytest.CaptureFixture[str]) -> None:
    """Pinned deliberately: human output goes out in the console's own encoding, which
    on Windows is a legacy codepage (an em dash becomes cp1252 0x97, not UTF-8). A
    consumer decoding the pipe as UTF-8 would break on it. Escaping to \\uXXXX makes the
    payload encode identically under every codepage while parsing back unchanged."""
    emit.json_ok("find", note=text)
    out = capsys.readouterr().out

    assert out.isascii(), "JSON output must not contain raw non-ASCII characters"
    assert json.loads(out)["note"] == text  # round-trips to the original characters
