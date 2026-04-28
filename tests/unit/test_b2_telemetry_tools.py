"""Unit tests for ADR-0033 Phase B2 — telemetry + forensics tools.

Covers:
- ueba_track.v1            (pure-python, time-windowed)
- port_scan_local.v1       (real socket against loopback)
- traffic_flow_local.v1    (subprocess-mocked OS shellout)
- evidence_collect.v1      (tarball assembly + manifest)
"""
from __future__ import annotations

import asyncio
import json
import socket
import tarfile
from pathlib import Path
from unittest import mock

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin import (
    EvidenceCollectTool,
    PortScanLocalTool,
    TrafficFlowLocalTool,
    UebaTrackTool,
)


def _run(coro):
    return asyncio.run(coro)


def _ctx(**kw):
    base = dict(
        instance_id="inst123abc", agent_dna="x" * 12,
        role="observer", genre="security_mid", session_id="s",
    )
    base.update(kw)
    return ToolContext(**base)


# ===========================================================================
# ueba_track.v1
# ===========================================================================
class TestUebaTrack:
    def test_validation_refusals(self):
        for bad in [
            {},
            {"events": "x"},
            {"events": [{}], "window": "month"},
            {"events": [{}], "features": [1, 2]},
        ]:
            with pytest.raises(ToolValidationError):
                UebaTrackTool().validate(bad)

    def test_groups_by_user_and_window(self):
        events = [
            {"user": "alice", "timestamp": "2026-04-27T10:15:00Z", "action": "x"},
            {"user": "alice", "timestamp": "2026-04-27T10:45:00Z", "action": "y"},
            {"user": "alice", "timestamp": "2026-04-27T11:05:00Z", "action": "x"},
            {"user": "bob",   "timestamp": "2026-04-27T10:30:00Z", "action": "x"},
        ]
        result = _run(UebaTrackTool().execute({"events": events}, _ctx()))
        assert result.output["user_count"] == 2
        alice = result.output["users"]["alice"]
        assert alice["total_events"] == 3
        assert alice["active_windows"] == 2  # two distinct hours

    def test_skipped_when_user_or_timestamp_missing(self):
        events = [
            {"user": "a", "timestamp": "2026-04-27T10:00:00Z"},
            {"user": "a"},                  # missing timestamp
            {"timestamp": "2026-04-27T11:00:00Z"},   # missing user
        ]
        result = _run(UebaTrackTool().execute({"events": events}, _ctx()))
        assert result.output["skipped"] == 2
        assert result.output["user_count"] == 1

    def test_window_floors_to_correct_boundary(self):
        events = [
            {"user": "a", "timestamp": "2026-04-27T15:30:00Z"},
            {"user": "a", "timestamp": "2026-04-27T16:30:00Z"},
        ]
        result = _run(UebaTrackTool().execute(
            {"events": events, "window": "day"}, _ctx(),
        ))
        # Same day → one window
        assert result.output["users"]["a"]["active_windows"] == 1


# ===========================================================================
# port_scan_local.v1
# ===========================================================================
class TestPortScanLocal:
    def test_refuses_non_loopback_target(self):
        for bad in ("8.8.8.8", "example.com", "10.0.0.1"):
            with pytest.raises(ToolValidationError, match="loopback"):
                PortScanLocalTool().validate({"target": bad})

    def test_validation_refuses_oversized_range(self):
        with pytest.raises(ToolValidationError, match="port_range"):
            PortScanLocalTool().validate({
                "port_range": {"start": 1, "end": 65535},
            })

    def test_finds_open_loopback_port(self):
        # Bind a real socket and confirm port_scan_local reports it open.
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        listen_port = server.getsockname()[1]
        try:
            result = _run(PortScanLocalTool().execute({
                "target": "127.0.0.1",
                "ports": [listen_port],
            }, _ctx()))
            assert listen_port in result.output["open"]
        finally:
            server.close()

    def test_closed_port_reported(self):
        # Pick a port that's almost certainly closed.
        # Use a high random port and verify it scores 'closed'.
        result = _run(PortScanLocalTool().execute({
            "target": "127.0.0.1",
            "ports": [1],   # almost certainly closed
        }, _ctx()))
        assert 1 not in result.output["open"]
        assert result.output["closed_count"] + result.output["filtered_or_open_count"] >= 1

    def test_localhost_normalizes_to_ipv4(self):
        result = _run(PortScanLocalTool().execute({
            "target": "localhost",
            "ports": [1],
        }, _ctx()))
        # The target should normalize to 127.0.0.1 in the output.
        assert result.output["target"] == "127.0.0.1"


# ===========================================================================
# traffic_flow_local.v1
# ===========================================================================
class TestTrafficFlowLocal:
    def test_lsof_parses_listen_and_established(self):
        fake = (
            "COMMAND  PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n"
            "sshd 1234 root 3u IPv4 0t0 TCP *:22 (LISTEN)\n"
            "curl 5678 alice 4u IPv4 0t0 TCP 127.0.0.1:54321->10.0.0.5:443 (ESTABLISHED)\n"
        ).encode()
        with mock.patch("shutil.which", side_effect=lambda b: "/fake/lsof" if b == "lsof" else None), \
             mock.patch("subprocess.run", return_value=mock.Mock(returncode=0, stdout=fake, stderr=b"")):
            result = _run(TrafficFlowLocalTool().execute({"backends": ["lsof"]}, _ctx()))
        assert result.output["count"] == 2
        states = {f["state"] for f in result.output["flows"]}
        assert states == {"LISTEN", "ESTABLISHED"}
        est = next(f for f in result.output["flows"] if f["state"] == "ESTABLISHED")
        assert est["src_port"] == 54321
        assert est["dst_port"] == 443
        assert est["dst"] == "10.0.0.5"

    def test_drops_time_wait_by_default(self):
        fake = (
            "COMMAND  PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n"
            "curl 1 root 4u IPv4 0t0 TCP 127.0.0.1:80->1.2.3.4:443 (TIME_WAIT)\n"
            "sshd 2 root 3u IPv4 0t0 TCP *:22 (LISTEN)\n"
        ).encode()
        with mock.patch("shutil.which", side_effect=lambda b: "/fake/lsof" if b == "lsof" else None), \
             mock.patch("subprocess.run", return_value=mock.Mock(returncode=0, stdout=fake, stderr=b"")):
            result = _run(TrafficFlowLocalTool().execute({"backends": ["lsof"]}, _ctx()))
        assert result.output["count"] == 1
        assert result.output["flows"][0]["state"] == "LISTEN"

    def test_include_timewait_keeps_them(self):
        fake = (
            "COMMAND  PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n"
            "curl 1 root 4u IPv4 0t0 TCP 127.0.0.1:80->1.2.3.4:443 (TIME_WAIT)\n"
        ).encode()
        with mock.patch("shutil.which", side_effect=lambda b: "/fake/lsof" if b == "lsof" else None), \
             mock.patch("subprocess.run", return_value=mock.Mock(returncode=0, stdout=fake, stderr=b"")):
            result = _run(TrafficFlowLocalTool().execute(
                {"backends": ["lsof"], "include_timewait": True}, _ctx(),
            ))
        assert result.output["count"] == 1

    def test_no_backend_available(self):
        with mock.patch("shutil.which", return_value=None):
            result = _run(TrafficFlowLocalTool().execute({}, _ctx()))
        assert result.output["count"] == 0
        assert result.output["backend_used"] is None


# ===========================================================================
# evidence_collect.v1
# ===========================================================================
class TestEvidenceCollect:
    def test_validation_refusals(self):
        for bad in [
            {"pids": "x"},
            {"pids": [0]},          # PID must be > 1
            {"pids": [-1]},
            {"pids": [True]},       # bool isn't a real PID
            {"label": ""},
            {"label": "x" * 100},
            {"label": "has spaces"},
        ]:
            with pytest.raises(ToolValidationError):
                EvidenceCollectTool().validate(bad)

    def test_no_evidence_dir_refuses(self):
        with pytest.raises(ToolValidationError, match="evidence_dir"):
            _run(EvidenceCollectTool().execute({}, _ctx()))

    def test_archive_includes_required_files(self, tmp_path):
        ctx = _ctx(constraints={"evidence_dir": str(tmp_path)})
        result = _run(EvidenceCollectTool().execute({"label": "test"}, ctx))
        assert "manifest.json" in result.output["files_included"]
        assert "ps_snapshot.txt" in result.output["files_included"]
        assert result.output["archive_sha256"].startswith("sha256:")
        # Tarball is real
        with tarfile.open(result.output["archive_path"], "r:gz") as t:
            assert "manifest.json" in t.getnames()
            mani = json.loads(t.extractfile("manifest.json").read())
            assert mani["label"] == "test"
            assert mani["caller_instance_id"] == "inst123abc"

    def test_label_appears_in_filename(self, tmp_path):
        ctx = _ctx(constraints={"evidence_dir": str(tmp_path)})
        result = _run(EvidenceCollectTool().execute({"label": "incident_42"}, ctx))
        assert "incident_42" in Path(result.output["archive_path"]).name


# ===========================================================================
# Registration sanity
# ===========================================================================
class TestRegistration:
    def test_all_telemetry_tools_register(self):
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.builtin import register_builtins
        reg = ToolRegistry()
        register_builtins(reg)
        for name in ("ueba_track", "port_scan_local",
                     "traffic_flow_local", "evidence_collect"):
            assert reg.has(name, "1"), f"{name} not registered"
