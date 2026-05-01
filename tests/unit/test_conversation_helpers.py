"""Unit tests for the extracted conversation_helpers module.

Phase C.1 (2026-04-30) split conversation-router pure helpers into a
dedicated module so they can be unit-tested in isolation. Previously
they were only exercised through integration tests against the live
HTTP path, which masked subtle bugs in prompt composition + ambient
gate-reading.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from forest_soul_forge.daemon.routers.conversation_helpers import (
    ambient_quota_used,
    build_ambient_prompt,
    build_conversation_prompt,
    conversation_out,
    participant_out,
    read_ambient_opt_in,
    resolve_active_provider,
    turn_out,
)


# ---------------------------------------------------------------------------
# Test stubs — duck-typed registry rows
# ---------------------------------------------------------------------------
@dataclass
class _Conv:
    conversation_id: str = "c1"
    domain: str = "test"
    operator_id: str = "op"
    created_at: str = "2026-04-30T00:00:00Z"
    last_turn_at: str | None = None
    status: str = "active"
    retention_policy: str = "full_7d"


@dataclass
class _Part:
    conversation_id: str = "c1"
    instance_id: str = "i1"
    joined_at: str = "2026-04-30T00:00:00Z"
    bridged_from: str | None = None


@dataclass
class _Turn:
    turn_id: str = "t1"
    conversation_id: str = "c1"
    speaker: str = "alex"
    addressed_to: str | None = None
    body: str | None = "hello"
    summary: str | None = None
    body_hash: str = "abc123"
    token_count: int | None = 5
    timestamp: str = "2026-04-30T00:00:00Z"
    model_used: str | None = None


# ===========================================================================
# Row → Pydantic adapters
# ===========================================================================
class TestAdapters:
    def test_conversation_out_round_trips(self):
        row = _Conv(domain="research", retention_policy="full_30d")
        out = conversation_out(row)
        assert out.conversation_id == "c1"
        assert out.domain == "research"
        assert out.retention_policy == "full_30d"

    def test_participant_out_preserves_bridged_from(self):
        row = _Part(bridged_from="other-domain")
        out = participant_out(row)
        assert out.bridged_from == "other-domain"

    def test_participant_out_none_bridged_from(self):
        out = participant_out(_Part())
        assert out.bridged_from is None

    def test_turn_out_includes_body_hash(self):
        row = _Turn(body_hash="sha-of-body")
        out = turn_out(row)
        assert out.body_hash == "sha-of-body"

    def test_turn_out_after_purge_body_none(self):
        """After Y7 retention sweep, body is None and summary populated."""
        row = _Turn(body=None, summary="condensed summary")
        out = turn_out(row)
        assert out.body is None
        assert out.summary == "condensed summary"
        assert out.body_hash  # tamper-evidence still present


# ===========================================================================
# build_conversation_prompt — Y2/Y3 reactive turn
# ===========================================================================
class TestBuildConversationPrompt:
    def test_includes_agent_identity(self):
        p = build_conversation_prompt(
            agent_name="Atlas", agent_role="system_architect",
            domain="coding", turns=[],
        )
        assert "Atlas" in p
        assert "system_architect" in p
        assert "coding" in p

    def test_renders_turns_speaker_body(self):
        turns = [
            _Turn(speaker="alex", body="what's the plan?"),
            _Turn(speaker="atlas-id", body="we should refactor."),
        ]
        p = build_conversation_prompt(
            agent_name="Atlas", agent_role="x", domain="d", turns=turns,
        )
        assert "alex: what's the plan?" in p
        assert "atlas-id: we should refactor." in p

    def test_summarized_turns_marked(self):
        """Post-Y7-purge turns surface as `[summarized] <summary>`."""
        turns = [_Turn(speaker="alex", body=None, summary="discussed plan")]
        p = build_conversation_prompt(
            agent_name="X", agent_role="y", domain="d", turns=turns,
        )
        assert "[summarized] discussed plan" in p

    def test_purged_no_summary_fallback(self):
        turns = [_Turn(speaker="alex", body=None, summary=None)]
        p = build_conversation_prompt(
            agent_name="X", agent_role="y", domain="d", turns=turns,
        )
        assert "[summarized]" in p
        assert "no summary available" in p

    def test_anti_impersonation_clause(self):
        """Critical safety: prompt must instruct not to pretend to be
        another participant. Drift here = identity-confusion bugs."""
        p = build_conversation_prompt(
            agent_name="Atlas", agent_role="x", domain="d", turns=[],
        )
        assert "do not pretend" in p.lower()
        assert "speak only as yourself" in p.lower()


# ===========================================================================
# build_ambient_prompt — Y5 proactive nudge
# ===========================================================================
class TestBuildAmbientPrompt:
    def test_nudge_kind_in_prompt(self):
        p = build_ambient_prompt(
            agent_name="A", agent_role="r", domain="d",
            nudge_kind="check_in", turns=[],
        )
        assert "check_in" in p

    def test_distinguishes_from_reactive(self):
        """The prompt MUST tell the agent this is NOT a reply — that's
        the load-bearing distinction between ambient and reactive."""
        p = build_ambient_prompt(
            agent_name="A", agent_role="r", domain="d",
            nudge_kind="x", turns=[],
        )
        assert "NOT" in p  # negation flag for reactive framing
        # And concrete framing of the proactive contribution:
        assert "proactively" in p.lower()
        assert "NEW contribution" in p or "new contribution" in p

    def test_caps_response_length(self):
        p = build_ambient_prompt(
            agent_name="A", agent_role="r", domain="d",
            nudge_kind="x", turns=[],
        )
        assert "1-3 sentence" in p

    def test_anti_impersonation_clause(self):
        p = build_ambient_prompt(
            agent_name="A", agent_role="r", domain="d",
            nudge_kind="x", turns=[],
        )
        assert "Don't pretend to be another participant" in p


# ===========================================================================
# read_ambient_opt_in — opt-in gate reader
# ===========================================================================
class TestReadAmbientOptIn:
    def test_missing_file_returns_false(self, tmp_path):
        assert read_ambient_opt_in(tmp_path / "missing.yaml") is False

    def test_opted_in_returns_true(self, tmp_path):
        p = tmp_path / "const.yaml"
        p.write_text(
            "interaction_modes:\n  ambient_opt_in: true\n",
            encoding="utf-8",
        )
        assert read_ambient_opt_in(p) is True

    def test_explicit_opt_out_returns_false(self, tmp_path):
        p = tmp_path / "const.yaml"
        p.write_text(
            "interaction_modes:\n  ambient_opt_in: false\n",
            encoding="utf-8",
        )
        assert read_ambient_opt_in(p) is False

    def test_no_interaction_modes_block_returns_false(self, tmp_path):
        """The default — agents born without the explicit opt-in are
        NOT eligible for ambient nudges. This is the primary safety
        invariant for ambient mode (structural opt-in, not opt-out)."""
        p = tmp_path / "const.yaml"
        p.write_text("schema_version: 1\nrole_base: x\n", encoding="utf-8")
        assert read_ambient_opt_in(p) is False

    def test_malformed_yaml_returns_false(self, tmp_path):
        p = tmp_path / "broken.yaml"
        p.write_text("interaction_modes: {not closed", encoding="utf-8")
        assert read_ambient_opt_in(p) is False

    def test_interaction_modes_not_a_dict_returns_false(self, tmp_path):
        p = tmp_path / "const.yaml"
        p.write_text("interaction_modes: just a string\n", encoding="utf-8")
        assert read_ambient_opt_in(p) is False


# ===========================================================================
# ambient_quota_used — count nudges in last 24h
# ===========================================================================
class _StubChainEntry:
    def __init__(self, *, event_type, timestamp, event_data):
        self.event_type = event_type
        self.timestamp = timestamp
        self.event_data = event_data


class _StubAuditChain:
    def __init__(self, entries):
        self._entries = entries

    def tail(self, n):
        return list(self._entries)


class TestAmbientQuotaUsed:
    def _now(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _ago(self, hours: int) -> str:
        return (
            datetime.now(timezone.utc) - timedelta(hours=hours)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_no_entries_returns_zero(self):
        chain = _StubAuditChain([])
        assert ambient_quota_used(
            audit_chain=chain, instance_id="i1", conversation_id="c1",
        ) == 0

    def test_counts_recent_matching_entries(self):
        entries = [
            _StubChainEntry(
                event_type="ambient_nudge",
                timestamp=self._now(),
                event_data={"instance_id": "i1", "conversation_id": "c1"},
            ),
            _StubChainEntry(
                event_type="ambient_nudge",
                timestamp=self._ago(2),
                event_data={"instance_id": "i1", "conversation_id": "c1"},
            ),
        ]
        chain = _StubAuditChain(entries)
        assert ambient_quota_used(
            audit_chain=chain, instance_id="i1", conversation_id="c1",
        ) == 2

    def test_filters_other_instance(self):
        entries = [_StubChainEntry(
            event_type="ambient_nudge",
            timestamp=self._now(),
            event_data={"instance_id": "OTHER", "conversation_id": "c1"},
        )]
        assert ambient_quota_used(
            audit_chain=_StubAuditChain(entries),
            instance_id="i1", conversation_id="c1",
        ) == 0

    def test_filters_other_conversation(self):
        entries = [_StubChainEntry(
            event_type="ambient_nudge",
            timestamp=self._now(),
            event_data={"instance_id": "i1", "conversation_id": "OTHER"},
        )]
        assert ambient_quota_used(
            audit_chain=_StubAuditChain(entries),
            instance_id="i1", conversation_id="c1",
        ) == 0

    def test_filters_other_event_type(self):
        entries = [_StubChainEntry(
            event_type="conversation_turn",  # not an ambient_nudge
            timestamp=self._now(),
            event_data={"instance_id": "i1", "conversation_id": "c1"},
        )]
        assert ambient_quota_used(
            audit_chain=_StubAuditChain(entries),
            instance_id="i1", conversation_id="c1",
        ) == 0

    def test_excludes_entries_older_than_24h(self):
        """Window is 24h; older entries are out of quota scope."""
        entries = [_StubChainEntry(
            event_type="ambient_nudge",
            timestamp=self._ago(25),
            event_data={"instance_id": "i1", "conversation_id": "c1"},
        )]
        assert ambient_quota_used(
            audit_chain=_StubAuditChain(entries),
            instance_id="i1", conversation_id="c1",
        ) == 0

    def test_short_circuits_on_old_entries(self):
        """The helper breaks the loop when it sees an older-than-24h
        entry. Tail is newest-first, so anything past the cutoff is
        guaranteed to be older too."""
        # Mix of recent + old; old must NOT count even though we
        # include several after the break.
        entries = [
            _StubChainEntry(
                event_type="ambient_nudge",
                timestamp=self._now(),
                event_data={"instance_id": "i1", "conversation_id": "c1"},
            ),
            _StubChainEntry(
                event_type="ambient_nudge",
                timestamp=self._ago(48),
                event_data={"instance_id": "i1", "conversation_id": "c1"},
            ),
            _StubChainEntry(
                event_type="ambient_nudge",
                timestamp=self._ago(72),
                event_data={"instance_id": "i1", "conversation_id": "c1"},
            ),
        ]
        assert ambient_quota_used(
            audit_chain=_StubAuditChain(entries),
            instance_id="i1", conversation_id="c1",
        ) == 1


# ===========================================================================
# resolve_active_provider — best-effort lookup
# ===========================================================================
class _StubRequest:
    def __init__(self, providers=None, raises=None):
        self.app = type("App", (), {"state": type("St", (), {})()})()
        if providers is not None:
            self.app.state.providers = providers
        self._raises = raises


class _StubProviderRegistry:
    def __init__(self, *, active_returns=None, raises=None):
        self._active = active_returns
        self._raises = raises

    def active(self):
        if self._raises:
            raise self._raises
        return self._active


class TestResolveActiveProvider:
    def test_no_providers_attribute_returns_none(self):
        req = _StubRequest()
        assert resolve_active_provider(req) is None

    def test_provider_active_returned(self):
        provider = object()
        req = _StubRequest(providers=_StubProviderRegistry(active_returns=provider))
        assert resolve_active_provider(req) is provider

    def test_active_raising_returns_none(self):
        """Best-effort — never propagate exceptions out. Tools that
        don't need a provider tolerate None."""
        req = _StubRequest(providers=_StubProviderRegistry(
            raises=RuntimeError("provider unavailable"),
        ))
        assert resolve_active_provider(req) is None
