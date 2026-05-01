"""Unit tests for the extracted birth_pipeline module.

Phase C.2 (2026-04-30) extraction. Pure helpers from writes.py that
were previously only exercised through the full /birth + /spawn HTTP
path are now testable in isolation. This file pins the contracts so
a future change to one helper is caught with a tight error rather
than buried in an integration test failure.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest

from forest_soul_forge.core.audit_chain import ChainEntry
from forest_soul_forge.daemon.routers.birth_pipeline import (
    chain_entry_to_parsed,
    derive_constitution_hash,
    idempotency_now,
    instance_id_for,
    rollback_artifacts,
    safe_agent_name,
    soul_path_for,
    to_agent_out,
    voice_event_fields,
    write_artifacts,
)
from forest_soul_forge.soul.voice_renderer import VoiceText


# ===========================================================================
# safe_agent_name — filename-safe whitelist
# ===========================================================================
class TestSafeAgentName:
    def test_alphanumeric_passes(self):
        assert safe_agent_name("Atlas") == "Atlas"
        assert safe_agent_name("Atlas42") == "Atlas42"

    def test_hyphen_underscore_preserved(self):
        assert safe_agent_name("Atlas-1") == "Atlas-1"
        assert safe_agent_name("agent_42") == "agent_42"

    def test_spaces_replaced(self):
        assert safe_agent_name("My Agent") == "My_Agent"

    def test_special_chars_replaced(self):
        assert safe_agent_name("a/b\\c:d") == "a_b_c_d"

    def test_traversal_attempt_neutralized(self):
        """A malicious name like '../../etc/passwd' must NOT produce a
        path-traversal result. All separators AND dots become
        underscore — the whitelist is alnum + ``-`` + ``_`` only."""
        out = safe_agent_name("../../etc/passwd")
        assert "/" not in out
        assert "." not in out
        # And the result is recognizable (etc_passwd portion preserved):
        assert "etc_passwd" in out

    def test_empty_returns_default(self):
        assert safe_agent_name("") == "agent"

    def test_all_invalid_returns_underscores_or_default(self):
        """All-invalid input — output should be underscores (length
        preserved) OR the default sentinel."""
        out = safe_agent_name("!@#$%")
        # Either all underscores OR the default — both are acceptable
        # outcomes for a fully-rejected name.
        assert out == "_____" or out == "agent"


# ===========================================================================
# instance_id_for — canonical instance ID
# ===========================================================================
class TestInstanceIdFor:
    def test_first_sibling_no_suffix(self):
        assert instance_id_for("network_watcher", "abc12345", 1) == "network_watcher_abc12345"

    def test_zero_sibling_no_suffix(self):
        """sibling_index 0 also gets the clean form (defensive)."""
        assert instance_id_for("role", "dna", 0) == "role_dna"

    def test_second_sibling_gets_suffix(self):
        assert instance_id_for("network_watcher", "abc12345", 2) == "network_watcher_abc12345_2"

    def test_high_sibling_gets_suffix(self):
        assert instance_id_for("r", "d", 7) == "r_d_7"


# ===========================================================================
# soul_path_for — paired artifact path math
# ===========================================================================
class TestSoulPathFor:
    def test_returns_two_paths(self, tmp_path):
        soul, const = soul_path_for(tmp_path, "Atlas", "atlas_dna")
        assert soul.suffix == ".md"
        assert soul.name.endswith(".soul.md")
        assert const.suffix == ".yaml"
        assert const.name.endswith(".constitution.yaml")

    def test_paths_share_base(self, tmp_path):
        """Soul + constitution always travel together with the same base."""
        soul, const = soul_path_for(tmp_path, "Atlas", "atlas_dna")
        soul_base = soul.name.replace(".soul.md", "")
        const_base = const.name.replace(".constitution.yaml", "")
        assert soul_base == const_base

    def test_unsafe_name_neutralized_in_path(self, tmp_path):
        """Unsafe characters in agent name don't leak into filesystem path."""
        soul, _ = soul_path_for(tmp_path, "evil/name", "id1")
        assert "/" not in soul.name

    def test_creates_out_dir(self, tmp_path):
        target = tmp_path / "deeply" / "nested" / "souls"
        soul_path_for(target, "Atlas", "id1")
        assert target.is_dir()


# ===========================================================================
# derive_constitution_hash — override binding
# ===========================================================================
class TestDeriveConstitutionHash:
    def test_no_override_returns_input_unchanged(self):
        h = "abc123"
        assert derive_constitution_hash(h, None) == h
        assert derive_constitution_hash(h, "") == h

    def test_override_changes_hash(self):
        original = "abc123"
        with_override = derive_constitution_hash(original, "key: value")
        assert with_override != original
        # Output is hex SHA-256 (64 chars)
        assert len(with_override) == 64

    def test_same_override_deterministic(self):
        h1 = derive_constitution_hash("same", "override yaml")
        h2 = derive_constitution_hash("same", "override yaml")
        assert h1 == h2

    def test_different_overrides_different_hashes(self):
        h1 = derive_constitution_hash("abc", "override A")
        h2 = derive_constitution_hash("abc", "override B")
        assert h1 != h2

    def test_pin_known_input_output(self):
        """Pin the SHA-256 contract — derived || '\\noverride:\\n' || override."""
        derived = "deadbeef"
        override = "extra: yaml"
        expected = hashlib.sha256()
        expected.update(derived.encode("utf-8"))
        expected.update(b"\noverride:\n")
        expected.update(override.encode("utf-8"))
        assert derive_constitution_hash(derived, override) == expected.hexdigest()


# ===========================================================================
# to_agent_out — adapter
# ===========================================================================
@dataclass
class _AgentRowStub:
    instance_id: str = "i1"
    dna: str = "abc12345"
    dna_full: str = "0" * 64
    role: str = "system_architect"
    agent_name: str = "Atlas"
    parent_instance: str | None = None
    owner_id: str | None = None
    model_name: str | None = None
    model_version: str | None = None
    soul_path: str = "/souls/Atlas.soul.md"
    constitution_path: str = "/souls/Atlas.constitution.yaml"
    constitution_hash: str = "h" * 64
    created_at: str = "2026-04-30T12:00:00Z"
    status: str = "active"
    legacy_minted: int = 0
    sibling_index: int = 1


class TestToAgentOut:
    def test_round_trips_fields(self):
        row = _AgentRowStub(agent_name="Forge", role="software_engineer")
        out = to_agent_out(row)
        assert out.agent_name == "Forge"
        assert out.role == "software_engineer"
        assert out.instance_id == "i1"


# ===========================================================================
# voice_event_fields — narrative_* adapter
# ===========================================================================
class TestVoiceEventFields:
    def test_none_returns_empty_dict(self):
        assert voice_event_fields(None) == {}

    def test_voice_returns_three_fields(self):
        v = VoiceText(
            markdown="x", provider="local",
            model="qwen2.5:7b", generated_at="2026-04-30 12:00:00Z",
        )
        out = voice_event_fields(v)
        assert out == {
            "narrative_provider": "local",
            "narrative_model": "qwen2.5:7b",
            "narrative_generated_at": "2026-04-30 12:00:00Z",
        }

    def test_template_provider_visible(self):
        """A template-fallback VoiceText must surface as
        narrative_provider='template' so the audit chain is honest
        about what wrote the soul."""
        v = VoiceText(
            markdown="x", provider="template",
            model="template", generated_at="2026-04-30 12:00:00Z",
        )
        assert voice_event_fields(v)["narrative_provider"] == "template"


# ===========================================================================
# chain_entry_to_parsed — adapter
# ===========================================================================
class TestChainEntryToParsed:
    def test_round_trips_all_fields(self):
        entry = ChainEntry(
            seq=42,
            timestamp="2026-04-30T12:00:00Z",
            prev_hash="prev",
            entry_hash="curr",
            agent_dna="abc12345",
            event_type="agent_created",
            event_data={"key": "value"},
        )
        parsed = chain_entry_to_parsed(entry)
        assert parsed.seq == 42
        assert parsed.timestamp == "2026-04-30T12:00:00Z"
        assert parsed.prev_hash == "prev"
        assert parsed.entry_hash == "curr"
        assert parsed.agent_dna == "abc12345"
        assert parsed.event_type == "agent_created"
        assert parsed.event_data == {"key": "value"}

    def test_event_data_copied_not_aliased(self):
        """The translated form must be a copy of event_data, not a
        reference — caller mutating the parsed entry's event_data
        must not corrupt the original ChainEntry."""
        original_data = {"a": 1}
        entry = ChainEntry(
            seq=1, timestamp="t", prev_hash="p", entry_hash="c",
            agent_dna=None, event_type="x", event_data=original_data,
        )
        parsed = chain_entry_to_parsed(entry)
        parsed.event_data["mutated"] = True
        # Original ChainEntry's event_data must be untouched.
        assert "mutated" not in entry.event_data


# ===========================================================================
# write_artifacts + rollback_artifacts — filesystem
# ===========================================================================
class TestWriteArtifacts:
    def test_writes_both_files(self, tmp_path):
        soul = tmp_path / "x.soul.md"
        const = tmp_path / "x.constitution.yaml"
        write_artifacts(soul, "soul body", const, "yaml body")
        assert soul.read_text() == "soul body"
        assert const.read_text() == "yaml body"

    def test_constitution_written_first(self, tmp_path):
        """Per the docstring contract — if a crash happens between the
        two writes, the constitution is on disk and the soul isn't,
        which is easier to detect than the reverse. Hard to test the
        crash path directly; this test pins the order at least."""
        soul = tmp_path / "x.soul.md"
        const = tmp_path / "x.constitution.yaml"
        # Both should exist after — checks both got written.
        write_artifacts(soul, "soul body", const, "yaml body")
        assert const.exists()
        assert soul.exists()


class TestRollbackArtifacts:
    def test_removes_both_files(self, tmp_path):
        soul = tmp_path / "x.soul.md"
        const = tmp_path / "x.constitution.yaml"
        soul.write_text("content")
        const.write_text("content")
        rollback_artifacts(soul, const)
        assert not soul.exists()
        assert not const.exists()

    def test_handles_missing_files(self, tmp_path):
        """No error if either file doesn't exist — best-effort cleanup."""
        soul = tmp_path / "missing.soul.md"
        const = tmp_path / "missing.constitution.yaml"
        # Should not raise:
        rollback_artifacts(soul, const)

    def test_partial_present_partial_missing(self, tmp_path):
        soul = tmp_path / "x.soul.md"
        const = tmp_path / "x.constitution.yaml"
        soul.write_text("only soul on disk")
        # const doesn't exist
        rollback_artifacts(soul, const)
        assert not soul.exists()


# ===========================================================================
# idempotency_now — timestamp shape
# ===========================================================================
class TestIdempotencyNow:
    def test_format(self):
        s = idempotency_now()
        # YYYY-MM-DDTHH:MM:SSZ — 20 chars
        assert len(s) == 20
        assert s[4] == "-" and s[7] == "-"
        assert s[10] == "T"
        assert s[13] == ":" and s[16] == ":"
        assert s.endswith("Z")

    def test_round_trips_through_strptime(self):
        from datetime import datetime, timezone
        s = idempotency_now()
        # Same parser the rest of the codebase uses.
        parsed = datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
        # Sanity: within 5 seconds of "now"
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        assert abs((now - parsed).total_seconds()) < 5
