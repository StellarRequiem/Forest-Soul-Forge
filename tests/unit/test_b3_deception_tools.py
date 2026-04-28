"""Unit tests for ADR-0033 Phase B3 — deception layer (canary_token + honeypot_local).

Covers:
- canary_token.v1   (plant + check honeytokens, atime + hash dual-signal)
- honeypot_local.v1 (TCP listener that logs connection attempts)
"""
from __future__ import annotations

import asyncio
import os
import socket
import time
from pathlib import Path

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin import CanaryTokenTool, HoneypotLocalTool


def _run(coro):
    return asyncio.run(coro)


def _ctx(**kw):
    base = dict(
        instance_id="i", agent_dna="x" * 12,
        role="guardian", genre="security_high", session_id="s",
    )
    base.update(kw)
    return ToolContext(**base)


# ============================================================================
# canary_token.v1
# ============================================================================
class TestCanaryTokenValidation:
    @pytest.mark.parametrize("bad", [
        {},
        {"op": "unknown", "tokens": [{"path": "/x"}]},
        {"op": "plant"},
        {"op": "plant", "tokens": []},
        {"op": "plant", "tokens": [{"path": "relative/p"}]},
        {"op": "plant", "tokens": [{"path": "/a"}, {"path": "/a"}]},
        {"op": "plant", "tokens": [{"path": "/a", "content": "x" * 5000}]},
        {"op": "plant", "tokens": [{"path": "/a", "content": 123}]},
        {"op": "plant", "tokens": [{"path": "/a", "label": 123}]},
        {"op": "check", "tokens": [{"path": "/x"}]},
        {"op": "check", "tokens": [{"path": "/x"}], "baseline": "bad"},
        {"op": "plant", "tokens": [{"path": "/x"}], "atime_drift_seconds": -1},
        {"op": "plant", "tokens": [{"path": "/x"}], "atime_drift_seconds": True},
    ])
    def test_validation_refusals(self, bad):
        with pytest.raises(ToolValidationError):
            CanaryTokenTool().validate(bad)

    def test_oversize_token_list_refused(self):
        with pytest.raises(ToolValidationError, match="tokens"):
            CanaryTokenTool().validate({
                "op": "plant",
                "tokens": [{"path": f"/p/{i}"} for i in range(60)],
            })


class TestCanaryTokenPlantCheck:
    def test_plant_writes_default_content(self, tmp_path):
        p = tmp_path / "creds.txt"
        result = _run(CanaryTokenTool().execute({
            "op": "plant",
            "tokens": [{"path": str(p), "label": "creds"}],
        }, _ctx()))
        assert result.output["results"][0]["status"] == "planted"
        # File exists with the canary banner.
        body = p.read_text()
        assert "FSF-CANARY-DO-NOT-USE" in body
        assert "label=creds" in body
        # Hash + atime + size all populated.
        entry = result.output["results"][0]
        assert entry["hash"].startswith("sha256:")
        assert entry["atime_unix"] > 0
        assert entry["size"] > 0

    def test_plant_uses_supplied_content(self, tmp_path):
        p = tmp_path / "key.txt"
        result = _run(CanaryTokenTool().execute({
            "op": "plant",
            "tokens": [{"path": str(p), "content": "AKIA-FAKE-KEY"}],
        }, _ctx()))
        assert p.read_text() == "AKIA-FAKE-KEY"
        assert result.output["results"][0]["status"] == "planted"

    def test_plant_creates_parent_dirs(self, tmp_path):
        p = tmp_path / "deep" / "nested" / "tok.txt"
        result = _run(CanaryTokenTool().execute({
            "op": "plant",
            "tokens": [{"path": str(p)}],
        }, _ctx()))
        assert result.output["results"][0]["status"] == "planted"
        assert p.exists()

    def test_check_untouched(self, tmp_path):
        p = tmp_path / "a.txt"
        plant = _run(CanaryTokenTool().execute({
            "op": "plant", "tokens": [{"path": str(p)}],
        }, _ctx()))
        baseline = _baseline_from_plant(plant)
        # Check immediately — should be untouched.
        result = _run(CanaryTokenTool().execute({
            "op": "check", "tokens": [{"path": str(p)}],
            "baseline": baseline,
        }, _ctx()))
        assert result.output["results"][0]["status"] == "untouched"
        assert result.output["verdict"] == "ok"

    def test_check_modified_yields_critical(self, tmp_path):
        p = tmp_path / "a.txt"
        plant = _run(CanaryTokenTool().execute({
            "op": "plant", "tokens": [{"path": str(p)}],
        }, _ctx()))
        baseline = _baseline_from_plant(plant)
        # Tamper.
        p.write_text("TAMPERED CONTENT")
        result = _run(CanaryTokenTool().execute({
            "op": "check", "tokens": [{"path": str(p)}],
            "baseline": baseline,
        }, _ctx()))
        assert result.output["results"][0]["status"] == "modified"
        assert result.output["verdict"] == "critical"

    def test_check_vanished_yields_critical(self, tmp_path):
        p = tmp_path / "a.txt"
        plant = _run(CanaryTokenTool().execute({
            "op": "plant", "tokens": [{"path": str(p)}],
        }, _ctx()))
        baseline = _baseline_from_plant(plant)
        p.unlink()
        result = _run(CanaryTokenTool().execute({
            "op": "check", "tokens": [{"path": str(p)}],
            "baseline": baseline,
        }, _ctx()))
        assert result.output["results"][0]["status"] == "vanished"
        assert result.output["verdict"] == "critical"

    def test_check_no_baseline_for_token(self, tmp_path):
        p = tmp_path / "a.txt"
        p.write_text("present but unbaselined")
        # Provide a baseline dict that doesn't include p's path.
        result = _run(CanaryTokenTool().execute({
            "op": "check", "tokens": [{"path": str(p)}],
            "baseline": {"/some/other/path": {
                "hash": "sha256:" + "a" * 64,
                "atime_unix": 0, "size": 0,
            }},
        }, _ctx()))
        assert result.output["results"][0]["status"] == "no_baseline"

    def test_atime_drift_tolerance_keeps_untouched(self, tmp_path):
        p = tmp_path / "a.txt"
        plant = _run(CanaryTokenTool().execute({
            "op": "plant", "tokens": [{"path": str(p)}],
        }, _ctx()))
        baseline = _baseline_from_plant(plant)
        # Bump baseline atime back by 100s so a real check sees +100s
        # drift; tolerance of 200s should keep it untouched.
        baseline[str(p)]["atime_unix"] -= 100
        result = _run(CanaryTokenTool().execute({
            "op": "check", "tokens": [{"path": str(p)}],
            "baseline": baseline,
            "atime_drift_seconds": 200,
        }, _ctx()))
        # Hash unchanged, drift within tolerance → untouched.
        assert result.output["results"][0]["status"] == "untouched"

    def test_modification_beats_access_in_classification(self, tmp_path):
        # When BOTH atime moved AND hash changed, status=modified
        # (not 'accessed') — the worse signal wins.
        p = tmp_path / "a.txt"
        plant = _run(CanaryTokenTool().execute({
            "op": "plant", "tokens": [{"path": str(p)}],
        }, _ctx()))
        baseline = _baseline_from_plant(plant)
        time.sleep(1.05)  # ensure atime ticks past 1-second granularity
        p.write_text("changed")
        # touch atime by reading
        with open(p, "r") as f:
            f.read()
        result = _run(CanaryTokenTool().execute({
            "op": "check", "tokens": [{"path": str(p)}],
            "baseline": baseline,
        }, _ctx()))
        assert result.output["results"][0]["status"] == "modified"


def _baseline_from_plant(plant_result):
    return {
        t["path"]: {
            "hash":       t["hash"],
            "atime_unix": t["atime_unix"],
            "size":       t["size"],
        }
        for t in plant_result.output["results"]
        if t["hash"] is not None
    }


# ============================================================================
# honeypot_local.v1
# ============================================================================
class TestHoneypotLocalValidation:
    @pytest.mark.parametrize("bad", [
        {},
        {"port": 22, "duration_seconds": 5},                          # below min port
        {"port": 70000, "duration_seconds": 5},                       # above max port
        {"port": 2222, "duration_seconds": 0},
        {"port": 2222, "duration_seconds": 500},
        {"port": 2222, "duration_seconds": 5, "banner": "x" * 300},
        {"port": 2222, "duration_seconds": 5, "banner": 123},
        {"port": 2222, "duration_seconds": 5, "bind_host": 123},
        {"port": 2222, "duration_seconds": 5, "max_connections": 0},
        {"port": 2222, "duration_seconds": 5, "max_connections": 2000},
        {"port": True, "duration_seconds": 5},
    ])
    def test_validation_refusals(self, bad):
        with pytest.raises(ToolValidationError):
            HoneypotLocalTool().validate(bad)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class TestHoneypotLocalListener:
    def test_captures_connection_with_banner_and_payload(self):
        port = _free_port()

        async def run_test():
            tool_task = asyncio.create_task(HoneypotLocalTool().execute({
                "port": port,
                "duration_seconds": 2,
                "banner": "SSH-2.0-OpenSSH_8.4 (FAKE)\n",
            }, _ctx()))
            # Give the listener a moment to bind.
            await asyncio.sleep(0.3)
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                banner = await reader.read(80)
                writer.write(b"GET / HTTP/1.0\r\n\r\n")
                await writer.drain()
                await asyncio.sleep(0.1)
                writer.close()
                await writer.wait_closed()
            finally:
                pass
            return await tool_task

        result = _run(run_test())
        assert result.output["event_count"] == 1
        ev = result.output["events"][0]
        assert ev["src_ip"] == "127.0.0.1"
        assert ev["banner_sent"] is True
        assert ev["bytes_received_count"] == len(b"GET / HTTP/1.0\r\n\r\n")
        assert "GET /" in ev["bytes_received_preview"]
        assert result.output["ended_reason"] == "duration"

    def test_no_connection_ends_via_duration(self):
        port = _free_port()
        result = _run(HoneypotLocalTool().execute({
            "port": port, "duration_seconds": 1,
        }, _ctx()))
        assert result.output["event_count"] == 0
        assert result.output["ended_reason"] == "duration"

    def test_busy_port_refused(self):
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        try:
            port = busy.getsockname()[1]
            with pytest.raises(ToolValidationError, match="cannot bind"):
                _run(HoneypotLocalTool().execute({
                    "port": port, "duration_seconds": 1,
                }, _ctx()))
        finally:
            busy.close()

    def test_non_loopback_bind_flags_warning(self):
        port = _free_port()
        # Bind to 127.0.0.1 still — but pretend the operator passed
        # a non-loopback host. We can't actually bind to 0.0.0.0 in
        # most sandboxes; verify the warning fires by checking the
        # 'skipped' field from a synthetic call. The bind itself
        # may or may not succeed, so accept either path.
        try:
            result = _run(HoneypotLocalTool().execute({
                "port": port, "duration_seconds": 1,
                "bind_host": "0.0.0.0",
            }, _ctx()))
            # If it bound, the warning should be in 'skipped'.
            assert any(s["name"] == "bind_warning"
                       for s in result.output["skipped"])
        except ToolValidationError:
            # Some sandboxes refuse non-loopback binds — that's fine.
            pass

    def test_max_connections_caps_capture(self):
        port = _free_port()

        async def run_test():
            tool_task = asyncio.create_task(HoneypotLocalTool().execute({
                "port": port,
                "duration_seconds": 5,
                "max_connections": 2,
            }, _ctx()))
            await asyncio.sleep(0.3)
            for _ in range(4):
                try:
                    reader, writer = await asyncio.open_connection("127.0.0.1", port)
                    writer.write(b"x")
                    await writer.drain()
                    await asyncio.sleep(0.05)
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:
                        pass
                except Exception:
                    pass
            return await tool_task

        result = _run(run_test())
        assert result.output["event_count"] <= 2
        # ended_reason may be "max_connections" or "duration" depending on
        # the timing of the cap check vs the deadline check; either is
        # acceptable as long as the cap held.


# ============================================================================
# Registration sanity
# ============================================================================
class TestRegistration:
    def test_both_tools_register(self):
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.builtin import register_builtins
        reg = ToolRegistry()
        register_builtins(reg)
        assert reg.has("canary_token", "1")
        assert reg.has("honeypot_local", "1")
