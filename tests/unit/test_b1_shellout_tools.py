"""Unit tests for ADR-0033 Phase B1 — OS-shellout low-tier tools.

Covers:
- patch_check.v1
- software_inventory.v1
- port_policy_audit.v1
- usb_device_audit.v1

Each tool's actual binary may not be present in CI; tests use
``unittest.mock.patch`` to stub ``shutil.which`` (so the tool
believes the binary is on PATH) and ``subprocess.run`` (so we
control the captured stdout/stderr/returncode).
"""
from __future__ import annotations

import asyncio
import json
from unittest import mock

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin import (
    PatchCheckTool,
    PortPolicyAuditTool,
    SoftwareInventoryTool,
    UsbDeviceAuditTool,
)


def _run(coro):
    return asyncio.run(coro)


def _ctx():
    return ToolContext(
        instance_id="x", agent_dna="x" * 12,
        role="observer", genre="security_low",
        session_id="s",
    )


def _fake_run(stdout: bytes, returncode: int = 0, stderr: bytes = b""):
    """Returns a side_effect callable that yields the same fake
    response regardless of what command was invoked."""
    return mock.Mock(returncode=returncode, stdout=stdout, stderr=stderr)


# ===========================================================================
# patch_check.v1
# ===========================================================================
class TestPatchCheck:
    def test_validation_unknown_backend_refused(self):
        with pytest.raises(ToolValidationError, match="bananas"):
            PatchCheckTool().validate({"backends": ["bananas"]})

    def test_no_backends_available_returns_skipped(self):
        # shutil.which → None for every backend means all four get
        # reported as binary_not_on_path; output is empty.
        with mock.patch("shutil.which", return_value=None):
            result = _run(PatchCheckTool().execute({}, _ctx()))
        assert result.output["updates"] == []
        skipped = {s["backend"] for s in result.output["backends_skipped"]}
        assert {"brew", "softwareupdate", "apt", "dnf"} == skipped

    def test_brew_parses_outdated_json(self):
        fake = json.dumps({
            "formulae": [
                {"name": "jq", "installed_versions": ["1.6"], "current_version": "1.7"},
                {"name": "rg", "installed_versions": ["13.0"], "current_version": "14.1"},
            ],
            "casks": [],
        })
        with mock.patch("shutil.which", return_value="/fake/brew"), \
             mock.patch("subprocess.run", return_value=_fake_run(fake.encode())):
            result = _run(PatchCheckTool().execute(
                {"backends": ["brew"]}, _ctx(),
            ))
        assert len(result.output["updates"]) == 2
        names = {u["name"] for u in result.output["updates"]}
        assert names == {"jq", "rg"}
        sources = {u["source"] for u in result.output["updates"]}
        assert sources == {"brew:formulae"}

    def test_softwareupdate_parses_label_pairs(self):
        fake = (
            "Software Update Tool\n"
            "\n"
            "Finding available software\n"
            "Software Update found the following new or updated software:\n"
            "* Label: Safari16.1-16.1\n"
            "\tTitle: Safari, Version: 16.1, Size: 100M, Recommended: YES\n"
            "* Label: macOSVentura13.0-13.0\n"
            "\tTitle: macOS Ventura, Version: 13.0, Size: 12G, Recommended: YES\n"
        )
        with mock.patch("shutil.which", return_value="/fake/softwareupdate"), \
             mock.patch("subprocess.run", return_value=_fake_run(fake.encode())):
            result = _run(PatchCheckTool().execute(
                {"backends": ["softwareupdate"]}, _ctx(),
            ))
        assert len(result.output["updates"]) == 2
        names = {u["name"] for u in result.output["updates"]}
        assert "Safari" in names

    def test_softwareupdate_no_updates_returns_empty(self):
        fake = "Software Update Tool\n\nNo new software available.\n"
        with mock.patch("shutil.which", return_value="/fake/softwareupdate"), \
             mock.patch("subprocess.run", return_value=_fake_run(fake.encode())):
            result = _run(PatchCheckTool().execute(
                {"backends": ["softwareupdate"]}, _ctx(),
            ))
        assert result.output["updates"] == []
        assert "softwareupdate" in result.output["backends_run"]

    def test_apt_parses_upgradable_lines(self):
        fake = (
            "Listing... Done\n"
            "curl/jammy 7.81.0-1ubuntu1.10 amd64 [upgradable from: 7.81.0-1ubuntu1.4]\n"
            "openssl/jammy 3.0.2-0ubuntu1.7 amd64 [upgradable from: 3.0.2-0ubuntu1.6]\n"
        )
        with mock.patch("shutil.which", return_value="/fake/apt"), \
             mock.patch("subprocess.run", return_value=_fake_run(fake.encode())):
            result = _run(PatchCheckTool().execute(
                {"backends": ["apt"]}, _ctx(),
            ))
        assert len(result.output["updates"]) == 2
        curl = next(u for u in result.output["updates"] if u["name"] == "curl")
        assert curl["current_version"] == "7.81.0-1ubuntu1.4"
        assert curl["available_version"] == "7.81.0-1ubuntu1.10"

    def test_dnf_exit_100_means_updates_available(self):
        fake = (
            "Last metadata expiration check: 0:00:42 ago.\n"
            "kernel.x86_64                  6.1.10-200.fc37        updates\n"
            "glibc.x86_64                   2.36-9.fc37            updates\n"
        )
        # dnf exits 100 to indicate "updates available" — not an error.
        with mock.patch("shutil.which", return_value="/fake/dnf"), \
             mock.patch("subprocess.run",
                        return_value=_fake_run(fake.encode(), returncode=100)):
            result = _run(PatchCheckTool().execute(
                {"backends": ["dnf"]}, _ctx(),
            ))
        assert len(result.output["updates"]) == 2

    def test_brew_json_parse_error_recorded(self):
        with mock.patch("shutil.which", return_value="/fake/brew"), \
             mock.patch("subprocess.run", return_value=_fake_run(b"not json{")):
            result = _run(PatchCheckTool().execute(
                {"backends": ["brew"]}, _ctx(),
            ))
        assert any(
            pe["backend"] == "brew" for pe in result.output["parse_errors"]
        )


# ===========================================================================
# software_inventory.v1
# ===========================================================================
class TestSoftwareInventory:
    def test_brew_list_versions(self):
        fake = b"jq 1.7\nrg 14.1.0\nfd 9.0.0\n"
        def _which(b):
            return "/fake/" + b if b == "brew" else None
        with mock.patch("shutil.which", side_effect=_which), \
             mock.patch("subprocess.run", return_value=_fake_run(fake)):
            result = _run(SoftwareInventoryTool().execute(
                {"backends": ["brew"]}, _ctx(),
            ))
        assert result.output["count"] == 3
        names = {it["name"] for it in result.output["items"]}
        assert names == {"jq", "rg", "fd"}

    def test_dpkg_tab_separated(self):
        fake = b"libc6\t2.35-0ubuntu3.1\nopenssl\t3.0.2-0ubuntu1.7\n"
        def _which(b):
            return "/fake/dpkg-query" if b == "dpkg-query" else None
        with mock.patch("shutil.which", side_effect=_which), \
             mock.patch("subprocess.run", return_value=_fake_run(fake)):
            result = _run(SoftwareInventoryTool().execute(
                {"backends": ["dpkg"]}, _ctx(),
            ))
        assert result.output["count"] == 2

    def test_rpm_tab_separated(self):
        fake = b"glibc\t2.36-9.fc37\nkernel\t6.1.10-200.fc37\n"
        def _which(b):
            return "/fake/rpm" if b == "rpm" else None
        with mock.patch("shutil.which", side_effect=_which), \
             mock.patch("subprocess.run", return_value=_fake_run(fake)):
            result = _run(SoftwareInventoryTool().execute(
                {"backends": ["rpm"]}, _ctx(),
            ))
        assert result.output["count"] == 2

    def test_no_backends_available(self):
        with mock.patch("shutil.which", return_value=None):
            result = _run(SoftwareInventoryTool().execute({}, _ctx()))
        assert result.output["count"] == 0
        assert len(result.output["backends_skipped"]) == 4


# ===========================================================================
# port_policy_audit.v1
# ===========================================================================
class TestPortPolicyAudit:
    def test_lsof_short_columns(self):
        fake = (
            "COMMAND  PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\n"
            "sshd    1234 root    3u  IPv4    0t0      TCP *:22 (LISTEN)\n"
            "nginx   2345 nginx   8u  IPv4    0t0      TCP 127.0.0.1:80 (LISTEN)\n"
        ).encode()
        def _which(b):
            return "/fake/lsof" if b == "lsof" else None
        with mock.patch("shutil.which", side_effect=_which), \
             mock.patch("subprocess.run", return_value=_fake_run(fake)):
            result = _run(PortPolicyAuditTool().execute(
                {"backends": ["lsof"]}, _ctx(),
            ))
        assert result.output["count"] == 2
        ports = {l["port"] for l in result.output["listeners"]}
        assert ports == {22, 80}
        # Both should be tcp
        assert all(l["proto"] == "tcp" for l in result.output["listeners"])

    def test_lsof_full_columns_with_device_hex(self):
        # Real lsof on macOS includes the DEVICE hex address. Parser
        # finds TCP/UDP by content, not column index.
        fake = (
            "COMMAND  PID USER   FD   TYPE             DEVICE SIZE/OFF NODE NAME\n"
            "sshd    1234 root    3u  IPv4 0xabcd1234567890ab      0t0  TCP *:22 (LISTEN)\n"
        ).encode()
        with mock.patch("shutil.which", side_effect=lambda b: "/fake/lsof" if b == "lsof" else None), \
             mock.patch("subprocess.run", return_value=_fake_run(fake)):
            result = _run(PortPolicyAuditTool().execute(
                {"backends": ["lsof"]}, _ctx(),
            ))
        assert result.output["count"] == 1
        assert result.output["listeners"][0]["port"] == 22

    def test_ss_extracts_pid_and_command(self):
        fake = (
            "State  Recv-Q Send-Q Local Address:Port  Peer Address:Port  Process\n"
            "LISTEN 0      128         0.0.0.0:22         0.0.0.0:*       "
            'users:(("sshd",pid=1234,fd=3))\n'
        ).encode()
        with mock.patch("shutil.which", side_effect=lambda b: "/fake/ss" if b == "ss" else None), \
             mock.patch("subprocess.run", return_value=_fake_run(fake)):
            result = _run(PortPolicyAuditTool().execute(
                {"backends": ["ss"]}, _ctx(),
            ))
        # ss is invoked twice (TCP + UDP), each returns the same fake.
        # The port 22 should appear with command='sshd' and pid=1234.
        sshd_listeners = [l for l in result.output["listeners"] if l["command"] == "sshd"]
        assert sshd_listeners
        assert sshd_listeners[0]["pid"] == 1234

    def test_no_backend_available(self):
        with mock.patch("shutil.which", return_value=None):
            result = _run(PortPolicyAuditTool().execute({}, _ctx()))
        assert result.output["backend_used"] is None
        assert result.output["count"] == 0
        assert len(result.output["skipped"]) == 3

    def test_ipv6_addr_parsed(self):
        fake = (
            "COMMAND  PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\n"
            "nginx   2345 nginx   8u  IPv6    0t0      TCP [::]:443 (LISTEN)\n"
        ).encode()
        with mock.patch("shutil.which", side_effect=lambda b: "/fake/lsof" if b == "lsof" else None), \
             mock.patch("subprocess.run", return_value=_fake_run(fake)):
            result = _run(PortPolicyAuditTool().execute(
                {"backends": ["lsof"]}, _ctx(),
            ))
        assert result.output["count"] == 1
        assert result.output["listeners"][0]["address"] == "::"
        assert result.output["listeners"][0]["port"] == 443


# ===========================================================================
# usb_device_audit.v1
# ===========================================================================
class TestUsbDeviceAudit:
    def test_system_profiler_flattens_tree(self):
        fake = json.dumps({"SPUSBDataType": [
            {"_name": "USB 3.0 Bus", "_items": [
                {"_name": "YubiKey 5 NFC",
                 "vendor_id": "0x1050",
                 "product_id": "0x0407",
                 "serial_num": "123ABC",
                 "location_id": "0x14100000",
                 "device_speed": "full_speed"},
            ]},
        ]}).encode()
        with mock.patch("shutil.which", side_effect=lambda b: "/fake/sp" if b == "system_profiler" else None), \
             mock.patch("subprocess.run", return_value=_fake_run(fake)):
            result = _run(UsbDeviceAuditTool().execute(
                {"backends": ["system_profiler"]}, _ctx(),
            ))
        assert result.output["count"] == 1
        d = result.output["devices"][0]
        assert d["vendor_id"] == "0x1050"
        assert d["product_id"] == "0x0407"
        assert d["serial"] == "123ABC"

    def test_lsusb_text_parsed(self):
        fake = (
            "Bus 001 Device 003: ID 1050:0407 Yubico.com YubiKey OTP+FIDO+CCID\n"
            "Bus 002 Device 002: ID 0bda:0411 Realtek 4-Port USB Hub\n"
            "Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub\n"
        ).encode()
        with mock.patch("shutil.which", side_effect=lambda b: "/fake/lsusb" if b == "lsusb" else None), \
             mock.patch("subprocess.run", return_value=_fake_run(fake)):
            result = _run(UsbDeviceAuditTool().execute(
                {"backends": ["lsusb"]}, _ctx(),
            ))
        assert result.output["count"] == 3
        vids = {d["vendor_id"] for d in result.output["devices"]}
        assert vids == {"0x1050", "0x0bda", "0x1d6b"}
        # location is preserved
        assert all("bus=" in d["location"] for d in result.output["devices"])

    def test_lsusb_malformed_line_recorded(self):
        fake = b"not a valid lsusb line\nBus 001 Device 003: ID 1050:0407 Yubico\n"
        with mock.patch("shutil.which", side_effect=lambda b: "/fake/lsusb" if b == "lsusb" else None), \
             mock.patch("subprocess.run", return_value=_fake_run(fake)):
            result = _run(UsbDeviceAuditTool().execute(
                {"backends": ["lsusb"]}, _ctx(),
            ))
        assert result.output["count"] == 1
        assert len(result.output["parse_errors"]) >= 1

    def test_no_backend_available(self):
        with mock.patch("shutil.which", return_value=None):
            result = _run(UsbDeviceAuditTool().execute({}, _ctx()))
        assert result.output["count"] == 0
        assert result.output["backend_used"] is None


# ===========================================================================
# Registration sanity
# ===========================================================================
class TestRegistration:
    def test_all_shellout_tools_register(self):
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.builtin import register_builtins
        reg = ToolRegistry()
        register_builtins(reg)
        for tool_name in ("patch_check", "software_inventory",
                          "port_policy_audit", "usb_device_audit"):
            assert reg.has(tool_name, "1"), f"{tool_name} not registered"
