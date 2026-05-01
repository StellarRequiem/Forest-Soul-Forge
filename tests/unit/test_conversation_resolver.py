"""Unit tests for the Y3/Y3.5 conversation addressee resolver.

The resolver is the pure-function core of ADR-003Y multi-agent
orchestration: given an operator turn body + room participants, decide
who responds. Y3 covered explicit addressing + @mention parsing; Y3.5
added a BM25-lite keyword-rank fallback when neither addressing nor
mentions hit. The 205 LoC of pure functions had only integration-level
coverage through the test_cross_subsystem trio — Phase A audit
2026-04-30, Finding T-2.

These tests exercise each resolution path in isolation against a
synthetic participants list so the behavior is pinned regardless of
downstream router changes.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from forest_soul_forge.daemon.routers.conversation_resolver import (
    parse_mentions,
    resolve_chain_continuation,
    resolve_initial_addressees,
)


# ---------------------------------------------------------------------------
# Test stubs — the resolver depends only on duck-typed shapes.
# ---------------------------------------------------------------------------
@dataclass
class _StubParticipant:
    instance_id: str


@dataclass
class _StubAgent:
    instance_id: str
    agent_name: str
    role: str = "system_architect"


def _make_lookup(agents_by_id):
    """Build a lookup callable that returns _StubAgent or None."""
    def _lookup(iid):
        return agents_by_id.get(iid)
    return _lookup


# ===========================================================================
# parse_mentions — @AgentName extraction
# ===========================================================================
class TestParseMentions:
    def test_empty_body_returns_empty(self):
        assert parse_mentions("", {"Atlas": "i1"}) == []

    def test_empty_name_to_id_returns_empty(self):
        assert parse_mentions("@Atlas plz", {}) == []

    def test_no_mentions_returns_empty(self):
        assert parse_mentions("normal sentence", {"Atlas": "i1"}) == []

    def test_single_mention(self):
        assert parse_mentions("hey @Atlas, what's up?", {"Atlas": "i1"}) == ["i1"]

    def test_multiple_mentions_in_order(self):
        out = parse_mentions(
            "@Atlas first then @Forge", {"Atlas": "i1", "Forge": "i2"},
        )
        assert out == ["i1", "i2"]

    def test_mention_dedupe_preserves_first_position(self):
        out = parse_mentions(
            "@Atlas one @Forge two @Atlas three",
            {"Atlas": "i1", "Forge": "i2"},
        )
        assert out == ["i1", "i2"]  # second @Atlas dropped

    def test_unknown_mention_silently_ignored(self):
        out = parse_mentions(
            "@Atlas and @Stranger", {"Atlas": "i1"},
        )
        assert out == ["i1"]  # @Stranger has no match → skipped

    def test_case_insensitive_fallback(self):
        out = parse_mentions(
            "@atlas (lowercase)", {"Atlas": "i1"},
        )
        assert out == ["i1"]

    def test_case_sensitive_preferred_over_fallback(self):
        """Exact match wins over case-insensitive fallback."""
        out = parse_mentions(
            "@Atlas",
            {"Atlas": "i1", "atlas": "i2"},  # both forms registered
        )
        assert out == ["i1"]  # exact-case match wins

    def test_underscore_and_hyphen_in_names(self):
        out = parse_mentions(
            "@Atlas_1777 and @code-reviewer-x",
            {"Atlas_1777": "i1", "code-reviewer-x": "i2"},
        )
        assert out == ["i1", "i2"]

    def test_mention_at_end_of_sentence_punctuation_excluded(self):
        """Trailing '.', ',', '?', '!' should NOT be part of the name."""
        out = parse_mentions(
            "Talking to @Atlas. Then @Forge, also @Sentinel?",
            {"Atlas": "i1", "Forge": "i2", "Sentinel": "i3"},
        )
        assert out == ["i1", "i2", "i3"]


# ===========================================================================
# resolve_initial_addressees — top-level dispatch resolution
# ===========================================================================
class TestResolveInitialAddressees:
    def test_explicit_addressed_to_wins(self):
        """Path 1: explicit addressing trumps body content."""
        participants = [_StubParticipant("i1"), _StubParticipant("i2")]
        agents = {
            "i1": _StubAgent("i1", "Atlas"),
            "i2": _StubAgent("i2", "Forge"),
        }
        out = resolve_initial_addressees(
            addressed_to=["i2", "i1"],
            body="@Atlas (ignored — explicit addressing wins)",
            participants=participants,
            agent_lookup_fn=_make_lookup(agents),
        )
        assert out == ["i2", "i1"]  # caller order preserved

    def test_explicit_addressed_to_dedupes(self):
        out = resolve_initial_addressees(
            addressed_to=["i1", "i2", "i1"],
            body="",
            participants=[],
            agent_lookup_fn=lambda x: None,
        )
        assert out == ["i1", "i2"]

    def test_mentions_in_body_when_no_explicit(self):
        """Path 2: @mentions resolve when addressed_to is empty/None."""
        participants = [_StubParticipant("i1"), _StubParticipant("i2")]
        agents = {
            "i1": _StubAgent("i1", "Atlas"),
            "i2": _StubAgent("i2", "Forge"),
        }
        out = resolve_initial_addressees(
            addressed_to=None,
            body="hey @Forge",
            participants=participants,
            agent_lookup_fn=_make_lookup(agents),
        )
        assert out == ["i2"]

    def test_keyword_rank_fallback_on_role_match(self):
        """Path 3 (Y3.5): no explicit, no mentions — pick by token overlap.

        The Y3.5 tokenizer is exact-match (no stemming) — see
        ``conversation_resolver._tokenize``. So the body must contain
        the exact role/name tokens to match. ``reviewer`` matches
        ``code_reviewer`` because ``_reviewer`` part of the role name
        survives the underscore split into {code, reviewer}.
        """
        participants = [
            _StubParticipant("atlas-id"),
            _StubParticipant("forge-id"),
            _StubParticipant("sentinel-id"),
        ]
        agents = {
            "atlas-id":    _StubAgent("atlas-id",    "Atlas",    role="system_architect"),
            "forge-id":    _StubAgent("forge-id",    "Forge",    role="software_engineer"),
            "sentinel-id": _StubAgent("sentinel-id", "Sentinel", role="code_reviewer"),
        }
        # Body contains exact-match "reviewer" — code_reviewer wins.
        out = resolve_initial_addressees(
            addressed_to=None,
            body="who is the reviewer for this change",
            participants=participants,
            agent_lookup_fn=_make_lookup(agents),
        )
        assert out == ["sentinel-id"]

    def test_keyword_rank_fallback_on_role_match_engineer(self):
        participants = [
            _StubParticipant("atlas-id"),
            _StubParticipant("forge-id"),
            _StubParticipant("sentinel-id"),
        ]
        agents = {
            "atlas-id":    _StubAgent("atlas-id",    "Atlas",    role="system_architect"),
            "forge-id":    _StubAgent("forge-id",    "Forge",    role="software_engineer"),
            "sentinel-id": _StubAgent("sentinel-id", "Sentinel", role="code_reviewer"),
        }
        out = resolve_initial_addressees(
            addressed_to=None,
            body="who can engineer the software fix",
            participants=participants,
            agent_lookup_fn=_make_lookup(agents),
        )
        assert out == ["forge-id"]

    def test_keyword_rank_first_participant_when_no_signal(self):
        """Body without tokens that match any participant → first."""
        participants = [
            _StubParticipant("a"),
            _StubParticipant("b"),
        ]
        agents = {
            "a": _StubAgent("a", "Alpha", role="custom_role"),
            "b": _StubAgent("b", "Beta",  role="other_role"),
        }
        out = resolve_initial_addressees(
            addressed_to=None,
            body="generic prose without role keywords",
            participants=participants,
            agent_lookup_fn=_make_lookup(agents),
        )
        # Both score 0; declaration order preserves first.
        assert out == ["a"]

    def test_no_participants_returns_empty(self):
        out = resolve_initial_addressees(
            addressed_to=None,
            body="anything",
            participants=[],
            agent_lookup_fn=lambda x: None,
        )
        assert out == []

    def test_unknown_agent_in_lookup_treated_as_zero_score(self):
        """A participant whose lookup returns None still ranks (with 0
        score) so we don't crash on registry inconsistency."""
        participants = [_StubParticipant("orphan"), _StubParticipant("real")]
        agents = {"real": _StubAgent("real", "Real", role="software_engineer")}
        out = resolve_initial_addressees(
            addressed_to=None,
            body="please engineer this",
            participants=participants,
            agent_lookup_fn=_make_lookup(agents),
        )
        # "real" should win — its role token "engineer" overlaps the body.
        assert out == ["real"]


# ===========================================================================
# resolve_chain_continuation — agent-to-agent @mention pass
# ===========================================================================
class TestResolveChainContinuation:
    def test_no_mentions_returns_empty(self):
        out = resolve_chain_continuation(
            last_responder_id="i1",
            last_response_body="just a flat reply",
            participants=[_StubParticipant("i1"), _StubParticipant("i2")],
            agent_lookup_fn=_make_lookup({
                "i1": _StubAgent("i1", "Atlas"),
                "i2": _StubAgent("i2", "Forge"),
            }),
        )
        assert out == []

    def test_self_mention_filtered(self):
        """Atlas mentions Atlas → no re-dispatch (DoS protection)."""
        out = resolve_chain_continuation(
            last_responder_id="i1",
            last_response_body="and @Atlas thinks more about it",
            participants=[_StubParticipant("i1")],
            agent_lookup_fn=_make_lookup({"i1": _StubAgent("i1", "Atlas")}),
        )
        assert out == []

    def test_mention_propagates_to_chain(self):
        out = resolve_chain_continuation(
            last_responder_id="i1",
            last_response_body="@Forge what do you think?",
            participants=[_StubParticipant("i1"), _StubParticipant("i2")],
            agent_lookup_fn=_make_lookup({
                "i1": _StubAgent("i1", "Atlas"),
                "i2": _StubAgent("i2", "Forge"),
            }),
        )
        assert out == ["i2"]

    def test_multiple_mentions_with_self_filtered(self):
        """Self-mention removed; others preserved in mention order."""
        out = resolve_chain_continuation(
            last_responder_id="i1",
            last_response_body="@Atlas and @Forge, then @Sentinel",
            participants=[
                _StubParticipant("i1"),
                _StubParticipant("i2"),
                _StubParticipant("i3"),
            ],
            agent_lookup_fn=_make_lookup({
                "i1": _StubAgent("i1", "Atlas"),
                "i2": _StubAgent("i2", "Forge"),
                "i3": _StubAgent("i3", "Sentinel"),
            }),
        )
        assert out == ["i2", "i3"]  # @Atlas filtered as self-mention
