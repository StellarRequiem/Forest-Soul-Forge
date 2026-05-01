"""Unit tests for ``dns_lookup.v1`` — ADR-0018 archetype kit primitive.

Implementation history: this tool was specced in ADR-0018 but lived in
the catalog YAML without an on-disk implementation through v0.1.0. The
2026-04-30 zombie-tool dissection IMPLEMENTed it (foundational primitive,
no substitute) before substituting other zombies and removing the
unimplementable ones. See ``docs/audits/2026-04-30-c1-zombie-tool-dissection.md``.

These tests use mocked socket calls so the suite doesn't depend on
DNS being reachable from the test environment.
"""
from __future__ import annotations

import asyncio
import socket
from unittest import mock

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.dns_lookup import DnsLookupTool


def _run(coro):
    return asyncio.run(coro)


def _ctx() -> ToolContext:
    return ToolContext(
        instance_id="i1", agent_dna="d" * 12,
        role="network_watcher", genre="observer",
        session_id="s1", constraints={},
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
class TestDnsLookupValidate:
    def test_neither_arg_rejected(self):
        with pytest.raises(ToolValidationError, match="hostname.*ip"):
            DnsLookupTool().validate({})

    def test_both_args_rejected(self):
        with pytest.raises(ToolValidationError, match="not both"):
            DnsLookupTool().validate({"hostname": "a.com", "ip": "1.1.1.1"})

    def test_hostname_must_be_string(self):
        with pytest.raises(ToolValidationError, match="hostname must be a string"):
            DnsLookupTool().validate({"hostname": 42})

    def test_hostname_empty_rejected(self):
        with pytest.raises(ToolValidationError, match="non-empty"):
            DnsLookupTool().validate({"hostname": "   "})

    def test_hostname_overlong_rejected(self):
        with pytest.raises(ToolValidationError, match="exceeds RFC"):
            DnsLookupTool().validate({"hostname": "a." * 200})

    def test_ip_must_be_string(self):
        with pytest.raises(ToolValidationError, match="ip must be a string"):
            DnsLookupTool().validate({"ip": 42})

    def test_ip_empty_rejected(self):
        with pytest.raises(ToolValidationError, match="non-empty"):
            DnsLookupTool().validate({"ip": ""})

    def test_ip_must_have_dot_or_colon(self):
        with pytest.raises(ToolValidationError, match="contain"):
            DnsLookupTool().validate({"ip": "garbage"})

    def test_timeout_out_of_range_rejected(self):
        with pytest.raises(ToolValidationError, match="timeout"):
            DnsLookupTool().validate({"hostname": "a.com", "timeout": 0})
        with pytest.raises(ToolValidationError, match="timeout"):
            DnsLookupTool().validate({"hostname": "a.com", "timeout": 99})

    def test_valid_forward_lookup_args(self):
        DnsLookupTool().validate({"hostname": "example.com"})

    def test_valid_reverse_lookup_args(self):
        DnsLookupTool().validate({"ip": "93.184.216.34"})
        DnsLookupTool().validate({"ip": "2606:2800:220:1::1"})  # IPv6


# ---------------------------------------------------------------------------
# Execute — happy paths (mocked socket)
# ---------------------------------------------------------------------------
class TestDnsLookupExecute:
    def test_forward_lookup_success(self):
        with mock.patch("socket.gethostbyname", return_value="93.184.216.34"):
            result = _run(DnsLookupTool().execute(
                {"hostname": "example.com"}, _ctx(),
            ))
        assert result.output["query"] == "example.com"
        assert result.output["kind"] == "forward"
        assert result.output["answer"] == "93.184.216.34"
        assert isinstance(result.output["elapsed_ms"], int)
        assert result.output["elapsed_ms"] >= 0

    def test_reverse_lookup_success(self):
        with mock.patch(
            "socket.gethostbyaddr",
            return_value=("example.com", [], ["93.184.216.34"]),
        ):
            result = _run(DnsLookupTool().execute(
                {"ip": "93.184.216.34"}, _ctx(),
            ))
        assert result.output["query"] == "93.184.216.34"
        assert result.output["kind"] == "reverse"
        assert result.output["answer"] == "example.com"

    def test_forward_lookup_failure_translated(self):
        with mock.patch("socket.gethostbyname", side_effect=socket.gaierror("no such host")):
            with pytest.raises(ToolValidationError, match="forward lookup failed"):
                _run(DnsLookupTool().execute(
                    {"hostname": "definitely-not-a-real-tld-zzz"}, _ctx(),
                ))

    def test_reverse_lookup_failure_translated(self):
        with mock.patch("socket.gethostbyaddr", side_effect=socket.herror("no PTR")):
            with pytest.raises(ToolValidationError, match="reverse lookup failed"):
                _run(DnsLookupTool().execute(
                    {"ip": "192.0.2.1"}, _ctx(),  # TEST-NET-1, no PTR
                ))

    def test_timeout_restored_after_call(self):
        """Tool must restore the prior socket default timeout even if
        the call raises. Otherwise it leaks process-wide state across
        adjacent tool calls (other agents, other tools)."""
        prior = socket.getdefaulttimeout()
        try:
            socket.setdefaulttimeout(7.5)  # something distinctive
            with mock.patch("socket.gethostbyname", return_value="1.1.1.1"):
                _run(DnsLookupTool().execute(
                    {"hostname": "a.com", "timeout": 3}, _ctx(),
                ))
            assert socket.getdefaulttimeout() == 7.5, (
                "tool leaked its timeout into the global socket state"
            )
        finally:
            socket.setdefaulttimeout(prior)

    def test_metadata_records_timeout(self):
        with mock.patch("socket.gethostbyname", return_value="1.1.1.1"):
            result = _run(DnsLookupTool().execute(
                {"hostname": "a.com", "timeout": 10}, _ctx(),
            ))
        assert result.metadata["timeout_used"] == 10

    def test_side_effect_summary_set(self):
        with mock.patch("socket.gethostbyname", return_value="1.1.1.1"):
            result = _run(DnsLookupTool().execute(
                {"hostname": "a.com"}, _ctx(),
            ))
        assert result.side_effect_summary is not None
        assert "dns_lookup" in result.side_effect_summary
        assert "a.com" in result.side_effect_summary
        assert "1.1.1.1" in result.side_effect_summary


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
class TestRegistration:
    def test_dns_lookup_registered_at_lifespan(self):
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.builtin import register_builtins
        reg = ToolRegistry()
        register_builtins(reg)
        assert reg.has("dns_lookup", "1")

    def test_protocol_attrs_present(self):
        t = DnsLookupTool()
        assert t.name == "dns_lookup"
        assert t.version == "1"
        assert t.side_effects == "network"
