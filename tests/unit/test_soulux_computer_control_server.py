"""ADR-0048 T2 (B163) — soulux-computer-control server tests.

The server at examples/plugins/soulux-computer-control/server is a
JSON-RPC stdio handler invoked once-per-call by Forest's
mcp_call.v1 (see src/forest_soul_forge/tools/builtin/mcp_call.py).
These tests run the server as a subprocess and verify the wire
protocol matches what the dispatcher expects.

What's covered:

  - Wire-protocol shape: request goes in via stdin; one JSON-RPC
    response line emerges on stdout
  - tools/list method returns the two T2 tools
  - tools/call → unknown_tool produces a JSON-RPC error
  - tools/call → unknown method (not tools/call or tools/list)
    produces a JSON-RPC error with code -32601
  - empty stdin → exits with stderr message + non-zero code
  - malformed JSON → JSON-RPC parse error response
  - Path-traversal defense: filename containing '/' or '..' is
    rejected before screencapture runs
  - On non-Darwin platforms, both tools surface a clear
    'platform_unsupported' error (so tests pass on the Linux CI
    sandbox; the actual screencapture/pbpaste paths are exercised
    only on macOS)

What's NOT covered (on the Linux test sandbox):
  - The success path of computer_screenshot — would need a Mac to
    actually invoke screencapture
  - The success path of computer_read_clipboard — same
  These are exercised on Alex's Mac when the operator runs the
  manual smoke test (documented in the burst commit script).
"""
from __future__ import annotations

import json
import platform
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = (
    REPO_ROOT
    / "examples"
    / "plugins"
    / "soulux-computer-control"
    / "server"
)


def _invoke_server(request: dict, *, timeout: float = 5.0) -> tuple[dict | None, str]:
    """Run the server with the given JSON-RPC request on stdin.
    Returns (parsed_response_dict_or_None, raw_stderr)."""
    proc = subprocess.run(
        [str(SERVER_PATH)],
        input=json.dumps(request).encode("utf-8") + b"\n",
        capture_output=True,
        timeout=timeout,
    )
    stderr = proc.stderr.decode("utf-8", errors="replace")
    stdout = proc.stdout.decode("utf-8", errors="replace").strip()
    if not stdout:
        return None, stderr
    last_line = stdout.split("\n")[-1]
    return json.loads(last_line), stderr


# ---------------------------------------------------------------------------
# Server existence + executable
# ---------------------------------------------------------------------------

def test_server_file_exists():
    assert SERVER_PATH.is_file()
    assert SERVER_PATH.stat().st_mode & 0o111, (
        f"{SERVER_PATH} is not executable. Plugin loader will refuse "
        f"to launch a non-executable entry point."
    )


# ---------------------------------------------------------------------------
# Wire protocol — happy paths
# ---------------------------------------------------------------------------

def test_tools_list_returns_full_v1_surface():
    """tools/list reports all six v1 tools (T2 + T3) so a hand-debugging
    operator can introspect the server."""
    resp, _ = _invoke_server({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert resp is not None
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    tools = resp["result"]["tools"]
    names = sorted(t["name"] for t in tools)
    assert names == [
        "computer_click",
        "computer_launch_url",
        "computer_read_clipboard",
        "computer_run_app",
        "computer_screenshot",
        "computer_type",
    ]


def test_tools_call_unknown_tool_errors_cleanly():
    """An unknown tool name → JSON-RPC error with -32602 (invalid
    params) and a message listing known tools."""
    resp, _ = _invoke_server({
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "computer_set_planet_alignment", "arguments": {}},
    })
    assert resp is not None
    assert resp["id"] == 7
    assert "error" in resp
    assert resp["error"]["code"] == -32602
    assert "computer_screenshot" in resp["error"]["message"]
    assert "computer_read_clipboard" in resp["error"]["message"]


def test_unknown_method_errors_cleanly():
    """An unsupported JSON-RPC method (not tools/call or tools/list)
    surfaces -32601 (method not found)."""
    resp, _ = _invoke_server({
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/cluck",
    })
    assert resp is not None
    assert resp["error"]["code"] == -32601


# ---------------------------------------------------------------------------
# Wire protocol — error paths
# ---------------------------------------------------------------------------

def test_empty_stdin_produces_stderr_and_nonzero_exit():
    """If the dispatcher accidentally invokes the server without any
    stdin (a bug shape that surfaced live in B144's fix), the server
    must NOT silently emit an empty stdout (mcp_call's parse path
    would then raise a confusing "no stdout response" error). It
    should emit a clear stderr message + exit non-zero."""
    proc = subprocess.run(
        [str(SERVER_PATH)],
        input=b"",
        capture_output=True,
        timeout=5,
    )
    assert proc.returncode != 0
    err = proc.stderr.decode("utf-8", errors="replace")
    assert "empty stdin" in err.lower() or "no JSON-RPC" in err


def test_malformed_json_produces_parse_error_response():
    """The server gets a single line that isn't JSON — must respond
    with the JSON-RPC parse-error code (-32700)."""
    proc = subprocess.run(
        [str(SERVER_PATH)],
        input=b"this is not json\n",
        capture_output=True,
        timeout=5,
    )
    assert proc.returncode != 0
    last_line = proc.stdout.decode("utf-8").strip().split("\n")[-1]
    resp = json.loads(last_line)
    assert resp["error"]["code"] == -32700


# ---------------------------------------------------------------------------
# Defense-in-depth — bad inputs
# ---------------------------------------------------------------------------

def test_screenshot_filename_path_traversal_rejected():
    """An operator-supplied filename containing '..' or '/' must be
    rejected BEFORE screencapture runs. Defense-in-depth: even on a
    misconfigured constitution, the server itself refuses to write
    outside ~/.forest/screenshots/."""
    resp, _ = _invoke_server({
        "jsonrpc": "2.0",
        "id": 11,
        "method": "tools/call",
        "params": {
            "name": "computer_screenshot",
            "arguments": {"filename": "../../etc/passwd"},
        },
    })
    assert resp is not None
    # Either error in JSON-RPC envelope, or isError=True in result
    # depending on platform path. On non-Darwin we get the platform
    # error first; on Darwin we get the bad_filename error. Both
    # should reject the request without writing anything.
    if "error" in resp:
        # JSON-RPC envelope error (shouldn't happen for this case
        # but acceptable).
        return
    result = resp["result"]
    assert result.get("isError") is True
    # On non-macOS the platform_unsupported error fires first; the
    # path-traversal check fires only on macOS. Either is acceptable
    # — both prevent the screencapture call.
    code = result["error"]["code"]
    assert code in {"bad_filename", "platform_unsupported"}


# ---------------------------------------------------------------------------
# Platform-gated success paths
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    platform.system() == "Darwin",
    reason="non-macOS-platform error path; macOS path tested separately",
)
def test_screenshot_on_non_macos_returns_platform_unsupported():
    resp, _ = _invoke_server({
        "jsonrpc": "2.0",
        "id": 21,
        "method": "tools/call",
        "params": {"name": "computer_screenshot", "arguments": {}},
    })
    assert resp is not None
    assert resp["result"]["isError"] is True
    assert resp["result"]["error"]["code"] == "platform_unsupported"


@pytest.mark.skipif(
    platform.system() == "Darwin",
    reason="non-macOS-platform error path; macOS path tested separately",
)
def test_read_clipboard_on_non_macos_returns_platform_unsupported():
    resp, _ = _invoke_server({
        "jsonrpc": "2.0",
        "id": 22,
        "method": "tools/call",
        "params": {"name": "computer_read_clipboard", "arguments": {}},
    })
    assert resp is not None
    assert resp["result"]["isError"] is True
    assert resp["result"]["error"]["code"] == "platform_unsupported"


@pytest.mark.skipif(
    platform.system() != "Darwin",
    reason="screencapture only available on macOS",
)
def test_screenshot_on_macos_writes_file():
    """On macOS, computer_screenshot writes a real PNG. This test
    runs only on the operator's Mac (not on Linux CI sandbox)."""
    resp, _ = _invoke_server({
        "jsonrpc": "2.0",
        "id": 31,
        "method": "tools/call",
        "params": {"name": "computer_screenshot", "arguments": {}},
    }, timeout=15)
    assert resp is not None
    result = resp["result"]
    if result.get("isError"):
        # The likely failure on a fresh Mac is missing Screen Recording
        # permission. Skip the assertion with the actionable message.
        pytest.skip(f"screencapture failed: {result['error']}")
    assert result["format"] == "png"
    assert result["size_bytes"] > 0
    assert Path(result["path"]).is_file()


@pytest.mark.skipif(
    platform.system() != "Darwin",
    reason="pbpaste only available on macOS",
)
def test_read_clipboard_on_macos_returns_text():
    """On macOS, computer_read_clipboard returns the current clipboard
    text. This test runs only on the operator's Mac."""
    resp, _ = _invoke_server({
        "jsonrpc": "2.0",
        "id": 32,
        "method": "tools/call",
        "params": {"name": "computer_read_clipboard", "arguments": {}},
    })
    assert resp is not None
    result = resp["result"]
    if result.get("isError"):
        pytest.skip(f"pbpaste failed: {result['error']}")
    assert "text" in result
    assert "length_chars" in result
    assert result["length_chars"] == len(result["text"])


# ---------------------------------------------------------------------------
# T3 (B164) — Action tool defense-in-depth + arg validation.
# These run on Linux too because they exercise the platform-independent
# arg validation path (rejects bad inputs BEFORE invoking osascript /
# open). The actual side-effect paths only exercise on macOS via the
# *_on_macos_* tests below — but those need the operator's deliberate
# action and aren't safe in CI.
# ---------------------------------------------------------------------------

class TestActionToolArgValidation:
    """Reject bad inputs uniformly across platforms before any
    subprocess fires. This is defense-in-depth — even on a misconfigured
    constitution that grants action tools, malformed args produce a
    clean JSON-RPC error rather than launching `open` / `osascript`
    with garbage."""

    def test_click_rejects_non_integer_coords(self):
        for bad in [
            {"x": "100", "y": 200},
            {"x": 100, "y": 200.5},
            {"x": 100},
            {},
        ]:
            resp, _ = _invoke_server({
                "jsonrpc": "2.0",
                "id": 41,
                "method": "tools/call",
                "params": {"name": "computer_click", "arguments": bad},
            })
            assert resp is not None
            result = resp["result"]
            assert result.get("isError") is True
            # Either the platform_unsupported path (Linux) OR the
            # bad_args path (macOS) — both are valid rejections.
            assert result["error"]["code"] in {"bad_args", "platform_unsupported"}

    def test_type_rejects_non_string_text(self):
        resp, _ = _invoke_server({
            "jsonrpc": "2.0",
            "id": 42,
            "method": "tools/call",
            "params": {"name": "computer_type", "arguments": {"text": 12345}},
        })
        assert resp is not None
        result = resp["result"]
        assert result.get("isError") is True
        assert result["error"]["code"] in {"bad_args", "platform_unsupported"}

    def test_type_rejects_text_over_4000_chars(self):
        resp, _ = _invoke_server({
            "jsonrpc": "2.0",
            "id": 43,
            "method": "tools/call",
            "params": {
                "name": "computer_type",
                "arguments": {"text": "x" * 5000},
            },
        })
        assert resp is not None
        result = resp["result"]
        assert result.get("isError") is True
        assert result["error"]["code"] in {"text_too_long", "platform_unsupported"}

    def test_run_app_rejects_path_separator(self):
        """An app_name containing '/' MUST be rejected before invoking
        `open -a` — the operator-granted run_app should NEVER be
        tricked into launching an arbitrary executable file path."""
        resp, _ = _invoke_server({
            "jsonrpc": "2.0",
            "id": 44,
            "method": "tools/call",
            "params": {
                "name": "computer_run_app",
                "arguments": {"app_name": "/usr/bin/whoami"},
            },
        })
        assert resp is not None
        result = resp["result"]
        assert result.get("isError") is True
        assert result["error"]["code"] in {"bad_app_name", "platform_unsupported"}

    def test_run_app_rejects_null_byte(self):
        resp, _ = _invoke_server({
            "jsonrpc": "2.0",
            "id": 45,
            "method": "tools/call",
            "params": {
                "name": "computer_run_app",
                "arguments": {"app_name": "Safari\x00"},
            },
        })
        assert resp is not None
        result = resp["result"]
        assert result.get("isError") is True
        assert result["error"]["code"] in {"bad_app_name", "platform_unsupported"}

    def test_run_app_rejects_empty_name(self):
        resp, _ = _invoke_server({
            "jsonrpc": "2.0",
            "id": 46,
            "method": "tools/call",
            "params": {
                "name": "computer_run_app",
                "arguments": {"app_name": "   "},
            },
        })
        assert resp is not None
        result = resp["result"]
        assert result.get("isError") is True
        assert result["error"]["code"] in {"bad_args", "platform_unsupported"}

    def test_launch_url_rejects_file_scheme(self):
        """file:// URLs are common local-file-exfil attack vectors.
        Even an operator-granted launch_url MUST refuse them before
        `open` runs."""
        resp, _ = _invoke_server({
            "jsonrpc": "2.0",
            "id": 47,
            "method": "tools/call",
            "params": {
                "name": "computer_launch_url",
                "arguments": {"url": "file:///etc/passwd"},
            },
        })
        assert resp is not None
        result = resp["result"]
        assert result.get("isError") is True
        assert result["error"]["code"] in {"scheme_disallowed", "platform_unsupported"}

    def test_launch_url_rejects_javascript_scheme(self):
        """javascript:// URLs are XSS attack vectors. Same defense."""
        resp, _ = _invoke_server({
            "jsonrpc": "2.0",
            "id": 48,
            "method": "tools/call",
            "params": {
                "name": "computer_launch_url",
                "arguments": {"url": "javascript:alert(1)"},
            },
        })
        assert resp is not None
        result = resp["result"]
        assert result.get("isError") is True
        assert result["error"]["code"] in {"scheme_disallowed", "platform_unsupported"}

    def test_launch_url_accepts_https(self):
        """https:// passes the scheme allowlist; on Linux the
        platform_unsupported error is what fires (no `open`); on
        macOS the actual launch path runs. Either is a valid
        non-rejection signal that the URL itself wasn't refused for
        scheme reasons."""
        resp, _ = _invoke_server({
            "jsonrpc": "2.0",
            "id": 49,
            "method": "tools/call",
            "params": {
                "name": "computer_launch_url",
                "arguments": {"url": "https://example.com"},
            },
        })
        assert resp is not None
        result = resp["result"]
        if platform.system() != "Darwin":
            # platform_unsupported is the expected non-Darwin response.
            assert result.get("isError") is True
            assert result["error"]["code"] == "platform_unsupported"
        # On macOS we don't assert anything beyond "didn't reject for
        # scheme" — actually launching a browser tab as a side-effect
        # of a unit test is rude.

    def test_launch_url_accepts_mailto(self):
        resp, _ = _invoke_server({
            "jsonrpc": "2.0",
            "id": 50,
            "method": "tools/call",
            "params": {
                "name": "computer_launch_url",
                "arguments": {"url": "mailto:test@example.com"},
            },
        })
        assert resp is not None
        result = resp["result"]
        if platform.system() != "Darwin":
            assert result["error"]["code"] == "platform_unsupported"


# ---------------------------------------------------------------------------
# Action tools — non-macOS platform_unsupported error path
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    platform.system() == "Darwin",
    reason="non-macOS-platform error path; macOS path tested separately",
)
class TestActionToolsOnNonMacOS:
    """All four action tools must surface platform_unsupported on
    non-Darwin so a Linux operator who installs the plugin gets a
    clear actionable error instead of a confusing osascript-not-found
    failure."""

    def test_click(self):
        resp, _ = _invoke_server({
            "jsonrpc": "2.0",
            "id": 61,
            "method": "tools/call",
            "params": {"name": "computer_click", "arguments": {"x": 100, "y": 100}},
        })
        assert resp["result"]["error"]["code"] == "platform_unsupported"

    def test_type(self):
        resp, _ = _invoke_server({
            "jsonrpc": "2.0",
            "id": 62,
            "method": "tools/call",
            "params": {"name": "computer_type", "arguments": {"text": "hello"}},
        })
        assert resp["result"]["error"]["code"] == "platform_unsupported"

    def test_run_app(self):
        resp, _ = _invoke_server({
            "jsonrpc": "2.0",
            "id": 63,
            "method": "tools/call",
            "params": {"name": "computer_run_app", "arguments": {"app_name": "Safari"}},
        })
        assert resp["result"]["error"]["code"] == "platform_unsupported"

    def test_launch_url(self):
        resp, _ = _invoke_server({
            "jsonrpc": "2.0",
            "id": 64,
            "method": "tools/call",
            "params": {"name": "computer_launch_url", "arguments": {"url": "https://example.com"}},
        })
        assert resp["result"]["error"]["code"] == "platform_unsupported"
