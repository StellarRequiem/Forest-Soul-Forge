"""Tests for Memory.find_candidate_pairs (ADR-0036 §2.1, T3a).

The candidate-pair pre-filter is the cheap first stage of the
Verifier Loop's scan. It returns pairs of memory entries that share
enough vocabulary to plausibly be talking about the same topic, so
the LLM-classification stage (T3b) can spend its budget on
high-quality candidates.

Coverage:
- TestTokenize         — _tokenize_for_overlap helper
- TestEligibility      — claim_type filter, instance_id filter,
                         since_iso freshness window
- TestOverlap          — min_overlap threshold, no-overlap empty,
                         shared_words shape
- TestDedup            — pairs already in memory_contradictions
                         excluded
- TestOrderingAndCap   — earlier/later by created_at, sorted by
                         desc overlap, max_pairs cap
"""
from __future__ import annotations

from pathlib import Path

import pytest

from forest_soul_forge.core.memory import Memory, _tokenize_for_overlap
from forest_soul_forge.registry import Registry
from tests.unit.conftest import seed_stub_agent


@pytest.fixture
def env(tmp_path):
    reg = Registry.bootstrap(tmp_path / "reg.sqlite")
    seed_stub_agent(reg, "agent_a")
    seed_stub_agent(reg, "agent_b")
    memory = Memory(conn=reg._conn)  # noqa: SLF001
    yield {"memory": memory, "registry": reg}
    reg.close()


def _seed(memory, instance_id, content, *, claim_type="preference"):
    return memory.append(
        instance_id=instance_id, agent_dna="d" * 12,
        content=content, layer="semantic",
        claim_type=claim_type,
    )


# ===========================================================================
# Tokenizer
# ===========================================================================
class TestTokenize:
    def test_basic_words(self):
        assert _tokenize_for_overlap("user prefers tea") == {"user", "prefers", "tea"}

    def test_lowercases(self):
        assert _tokenize_for_overlap("USER prefers Tea") == {"user", "prefers", "tea"}

    def test_drops_stopwords(self):
        out = _tokenize_for_overlap("the user is happy")
        assert "the" not in out
        assert "is" not in out
        assert out == {"user", "happy"}

    def test_drops_short_tokens(self):
        # 'is' (2 chars) drops via length check even before stopword check.
        out = _tokenize_for_overlap("a b cat dog")
        assert out == {"cat", "dog"}

    def test_punctuation_stripped(self):
        out = _tokenize_for_overlap("user said: 'tea, please!'")
        assert "tea" in out
        assert "please" in out

    def test_numbers_kept(self):
        # Numbers might be load-bearing for dates/years/etc. Don't drop.
        out = _tokenize_for_overlap("user prefers 2024 model")
        assert "2024" in out


# ===========================================================================
# Eligibility filters
# ===========================================================================
class TestEligibility:
    def test_only_eligible_claim_types(self, env):
        m = env["memory"]
        # observation + external_fact are NOT eligible per §2.1
        _seed(m, "agent_a", "user prefers tea", claim_type="observation")
        _seed(m, "agent_a", "user prefers coffee", claim_type="observation")
        pairs = m.find_candidate_pairs(instance_id="agent_a")
        assert pairs == []

    def test_eligible_claim_types_pair(self, env):
        m = env["memory"]
        _seed(m, "agent_a", "user prefers tea", claim_type="preference")
        _seed(m, "agent_a", "user prefers coffee", claim_type="preference")
        pairs = m.find_candidate_pairs(instance_id="agent_a")
        assert len(pairs) == 1

    def test_mixed_claim_types_can_pair(self, env):
        m = env["memory"]
        _seed(m, "agent_a", "user prefers tea", claim_type="preference")
        _seed(m, "agent_a", "user said tea", claim_type="user_statement")
        pairs = m.find_candidate_pairs(instance_id="agent_a")
        assert len(pairs) == 1
        assert {pairs[0]["earlier_claim_type"], pairs[0]["later_claim_type"]} == {
            "preference", "user_statement",
        }

    def test_only_same_instance(self, env):
        # Two entries on agent_a, two on agent_b with identical content.
        # Cross-agent pairs MUST NOT surface (cross-agent scan is v0.4).
        m = env["memory"]
        _seed(m, "agent_a", "user prefers tea")
        _seed(m, "agent_b", "user prefers tea")
        pairs_a = m.find_candidate_pairs(instance_id="agent_a")
        pairs_b = m.find_candidate_pairs(instance_id="agent_b")
        assert pairs_a == []
        assert pairs_b == []

    def test_since_iso_far_future_excludes_all(self, env):
        # since_iso semantic: strictly-after. A far-future cutoff
        # excludes everything regardless of when entries were written.
        # Direct test of the SQL filter — sidesteps the
        # second-resolution _now_iso (no microseconds).
        m = env["memory"]
        _seed(m, "agent_a", "user prefers tea morning")
        _seed(m, "agent_a", "user prefers tea evening")
        pairs = m.find_candidate_pairs(
            instance_id="agent_a",
            since_iso="2099-01-01T00:00:00Z",
        )
        assert pairs == []

    def test_since_iso_far_past_includes_all(self, env):
        m = env["memory"]
        _seed(m, "agent_a", "user prefers tea morning")
        _seed(m, "agent_a", "user prefers tea evening")
        pairs = m.find_candidate_pairs(
            instance_id="agent_a",
            since_iso="1999-01-01T00:00:00Z",
        )
        assert len(pairs) == 1


# ===========================================================================
# Overlap
# ===========================================================================
class TestOverlap:
    def test_no_overlap_returns_empty(self, env):
        m = env["memory"]
        _seed(m, "agent_a", "spaghetti carbonara recipe")
        _seed(m, "agent_a", "quantum entanglement physics")
        assert m.find_candidate_pairs(instance_id="agent_a") == []

    def test_min_overlap_one_word(self, env):
        m = env["memory"]
        _seed(m, "agent_a", "user prefers tea")
        _seed(m, "agent_a", "weather forecast tea")  # only 'tea' overlaps
        pairs = m.find_candidate_pairs(
            instance_id="agent_a", min_overlap=1,
        )
        assert len(pairs) == 1
        assert pairs[0]["shared_words"] == ["tea"]

    def test_min_overlap_two_default(self, env):
        m = env["memory"]
        # Single-word overlap should NOT pair under default min_overlap=2
        _seed(m, "agent_a", "user prefers tea")
        _seed(m, "agent_a", "weather forecast tea")
        assert m.find_candidate_pairs(instance_id="agent_a") == []

    def test_shared_words_sorted(self, env):
        m = env["memory"]
        _seed(m, "agent_a", "user prefers warm tea drink")
        _seed(m, "agent_a", "user dislikes warm tea evening")
        pairs = m.find_candidate_pairs(instance_id="agent_a", min_overlap=2)
        assert len(pairs) == 1
        # sorted alphabetically: ['tea', 'user', 'warm']
        assert pairs[0]["shared_words"] == sorted(pairs[0]["shared_words"])
        assert "tea" in pairs[0]["shared_words"]
        assert "warm" in pairs[0]["shared_words"]


# ===========================================================================
# Dedup against memory_contradictions
# ===========================================================================
class TestDedup:
    def test_already_flagged_pair_excluded(self, env):
        m = env["memory"]
        a = _seed(m, "agent_a", "user prefers tea morning")
        b = _seed(m, "agent_a", "user prefers tea evening different")
        # First scan: pair surfaces.
        pairs = m.find_candidate_pairs(instance_id="agent_a")
        assert len(pairs) == 1
        # Flag it.
        m.flag_contradiction(
            earlier_entry_id=a.entry_id, later_entry_id=b.entry_id,
            contradiction_kind="updated", detected_by="op",
        )
        # Re-scan: same pair should NOT surface.
        pairs2 = m.find_candidate_pairs(instance_id="agent_a")
        assert pairs2 == []

    def test_other_pairs_still_surface(self, env):
        m = env["memory"]
        a = _seed(m, "agent_a", "user prefers tea morning")
        b = _seed(m, "agent_a", "user prefers tea evening")
        c = _seed(m, "agent_a", "user prefers coffee morning evening")
        # Flag (a, b)
        m.flag_contradiction(
            earlier_entry_id=a.entry_id, later_entry_id=b.entry_id,
            contradiction_kind="direct", detected_by="op",
        )
        # (a, c) and (b, c) still candidate (overlap on user/prefers/morning/evening)
        pairs = m.find_candidate_pairs(instance_id="agent_a")
        flagged_pair = frozenset((a.entry_id, b.entry_id))
        for p in pairs:
            assert frozenset((p["earlier_entry_id"], p["later_entry_id"])) != flagged_pair


# ===========================================================================
# Ordering and max_pairs cap
# ===========================================================================
class TestOrderingAndCap:
    def test_earlier_later_by_created_at(self, env):
        import time
        m = env["memory"]
        a = _seed(m, "agent_a", "user prefers tea morning")
        time.sleep(0.01)
        b = _seed(m, "agent_a", "user prefers tea evening")
        pairs = m.find_candidate_pairs(instance_id="agent_a")
        assert len(pairs) == 1
        # a created first → earlier; b → later
        assert pairs[0]["earlier_entry_id"] == a.entry_id
        assert pairs[0]["later_entry_id"] == b.entry_id

    def test_sorted_by_desc_overlap(self, env):
        m = env["memory"]
        # Pair P1: {a, b} overlap 2 words
        a = _seed(m, "agent_a", "user prefers tea")
        b = _seed(m, "agent_a", "user prefers coffee")
        # Pair P2: {c, d} overlap 4 words (more)
        c = _seed(m, "agent_a", "user wants warm comfortable beverage")
        d = _seed(m, "agent_a", "user wants warm comfortable drink")
        pairs = m.find_candidate_pairs(instance_id="agent_a", min_overlap=2)
        # P2 has more overlap → comes first
        assert pairs[0]["overlap_size"] >= pairs[-1]["overlap_size"]
        assert pairs[0]["overlap_size"] == 4

    def test_max_pairs_cap(self, env):
        m = env["memory"]
        # 5 entries, all overlapping ('user prefers') → C(5,2)=10 pairs
        for i in range(5):
            _seed(m, "agent_a", f"user prefers thing-{i}-flavor")
        pairs = m.find_candidate_pairs(instance_id="agent_a", max_pairs=3)
        assert len(pairs) == 3

    def test_zero_max_pairs_returns_empty(self, env):
        m = env["memory"]
        _seed(m, "agent_a", "user prefers tea")
        _seed(m, "agent_a", "user prefers coffee")
        assert m.find_candidate_pairs(instance_id="agent_a", max_pairs=0) == []

    def test_single_entry_returns_empty(self, env):
        m = env["memory"]
        _seed(m, "agent_a", "user prefers tea")
        assert m.find_candidate_pairs(instance_id="agent_a") == []

    def test_no_entries_returns_empty(self, env):
        # No memory entries at all for this agent.
        m = env["memory"]
        assert m.find_candidate_pairs(instance_id="agent_a") == []
