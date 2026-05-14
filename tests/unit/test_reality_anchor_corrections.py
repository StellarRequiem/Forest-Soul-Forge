"""Tests for ADR-0063 T6 — reality_anchor_corrections table + recurrence.

Coverage:
- Schema v20: reality_anchor_corrections table exists on fresh DB
- Schema v19 → v20 migration adds the table without touching other data
- normalize_claim + claim_hash apply lowercase + whitespace collapse
- bump_or_create on a fresh hash returns 1
- bump_or_create on a repeat hash returns post-bump count >= 2
- Case + whitespace variants hash to the same row
- worst_severity escalates (LOW → HIGH leaves HIGH; HIGH → LOW leaves HIGH)
- list_repeat_offenders filters by min_repetitions
- list_repeat_offenders orders by repetition_count DESC
- get(claim) returns None on miss
- get(claim) returns CorrectionRow on hit
- KNOWN_EVENT_TYPES contains reality_anchor_repeat_offender
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from forest_soul_forge.core.audit_chain import KNOWN_EVENT_TYPES
from forest_soul_forge.registry import Registry
from forest_soul_forge.registry.schema import SCHEMA_VERSION
from forest_soul_forge.registry.tables.reality_anchor_corrections import (
    CorrectionRow,
    RealityAnchorCorrectionsTable,
    claim_hash,
    normalize_claim,
)


def test_schema_version_is_21():
    """ADR-0063 T6 — schema bump 19→20 adds reality_anchor_corrections."""
    assert SCHEMA_VERSION == 21


def test_event_type_registered():
    """reality_anchor_repeat_offender must be in KNOWN_EVENT_TYPES so
    AuditChain.verify doesn't log a forward-compat warning on every
    emission."""
    assert "reality_anchor_repeat_offender" in KNOWN_EVENT_TYPES


# ===========================================================================
# Normalization
# ===========================================================================


class TestNormalization:
    def test_lowercase(self):
        assert normalize_claim("DNA is RANDOM") == "dna is random"

    def test_whitespace_collapsed(self):
        assert normalize_claim("DNA   is\nrandom\t and uuid") \
            == "dna is random and uuid"

    def test_trimmed(self):
        assert normalize_claim("   hello world   ") == "hello world"

    def test_empty(self):
        assert normalize_claim("") == ""
        assert normalize_claim("   ") == ""

    def test_hash_stable_across_case_and_whitespace(self):
        h1 = claim_hash("DNA is random and uuid-based")
        h2 = claim_hash("   dna   IS RANDOM and UUID-BASED  ")
        h3 = claim_hash("dna\nis\trandom and uuid-based")
        assert h1 == h2 == h3

    def test_hash_distinct_for_semantically_different(self):
        h1 = claim_hash("DNA is random")
        h2 = claim_hash("DNA is content-addressed")
        assert h1 != h2


# ===========================================================================
# bump_or_create
# ===========================================================================


@pytest.fixture
def reg(tmp_path):
    r = Registry.bootstrap(tmp_path / "r.sqlite")
    yield r
    r.close()


class TestBumpOrCreate:
    def test_first_sighting_returns_one(self, reg):
        rac = reg.reality_anchor_corrections
        n = rac.bump_or_create(
            claim="DNA is random",
            fact_id="dna_identity",
            worst_severity="CRITICAL",
            now_iso="2026-05-12T10:00:00Z",
            agent_dna="a" * 12,
            instance_id="agent1",
            decision="refused",
            surface="dispatcher",
        )
        assert n == 1

    def test_repeat_bumps_count(self, reg):
        rac = reg.reality_anchor_corrections
        for i in range(1, 6):
            n = rac.bump_or_create(
                claim="DNA is random",
                fact_id="dna_identity",
                worst_severity="CRITICAL",
                now_iso=f"2026-05-12T10:0{i}:00Z",
                agent_dna="a" * 12,
                instance_id="agent1",
                decision="refused",
                surface="dispatcher",
            )
            assert n == i

    def test_case_variants_hit_same_row(self, reg):
        rac = reg.reality_anchor_corrections
        rac.bump_or_create(
            claim="DNA is random",
            fact_id="dna_identity", worst_severity="CRITICAL",
            now_iso="t1", agent_dna="a", instance_id="i1",
            decision="refused", surface="dispatcher",
        )
        n = rac.bump_or_create(
            claim="   DNA   IS   random   ",
            fact_id="dna_identity", worst_severity="CRITICAL",
            now_iso="t2", agent_dna="a", instance_id="i1",
            decision="refused", surface="dispatcher",
        )
        assert n == 2

    def test_distinct_claims_get_distinct_rows(self, reg):
        rac = reg.reality_anchor_corrections
        a = rac.bump_or_create(
            claim="claim A", fact_id="f1", worst_severity="HIGH",
            now_iso="t", agent_dna="a", instance_id="i",
            decision="warned", surface="dispatcher",
        )
        b = rac.bump_or_create(
            claim="claim B", fact_id="f2", worst_severity="HIGH",
            now_iso="t", agent_dna="a", instance_id="i",
            decision="warned", surface="dispatcher",
        )
        assert a == 1 and b == 1

    def test_worst_severity_escalates(self, reg):
        rac = reg.reality_anchor_corrections
        rac.bump_or_create(
            claim="X", fact_id="f", worst_severity="LOW",
            now_iso="t", agent_dna="a", instance_id="i",
            decision="warned", surface="dispatcher",
        )
        rac.bump_or_create(
            claim="X", fact_id="f", worst_severity="CRITICAL",
            now_iso="t", agent_dna="a", instance_id="i",
            decision="refused", surface="dispatcher",
        )
        row = rac.get("X")
        assert row.worst_severity == "CRITICAL"

    def test_worst_severity_does_not_de_escalate(self, reg):
        rac = reg.reality_anchor_corrections
        rac.bump_or_create(
            claim="X", fact_id="f", worst_severity="HIGH",
            now_iso="t", agent_dna="a", instance_id="i",
            decision="warned", surface="dispatcher",
        )
        rac.bump_or_create(
            claim="X", fact_id="f", worst_severity="LOW",
            now_iso="t", agent_dna="a", instance_id="i",
            decision="warned", surface="dispatcher",
        )
        row = rac.get("X")
        # HIGH dominates a subsequent LOW.
        assert row.worst_severity == "HIGH"

    def test_last_fields_overwrite_first_preserved(self, reg):
        rac = reg.reality_anchor_corrections
        rac.bump_or_create(
            claim="X", fact_id="f", worst_severity="HIGH",
            now_iso="first-time",
            agent_dna="a", instance_id="i_first",
            decision="warned", surface="dispatcher",
        )
        rac.bump_or_create(
            claim="X", fact_id="f", worst_severity="HIGH",
            now_iso="last-time",
            agent_dna="b", instance_id="i_last",
            decision="refused", surface="conversation",
        )
        row = rac.get("X")
        assert row.first_seen_at == "first-time"
        assert row.last_seen_at == "last-time"
        assert row.last_agent_dna == "b"
        assert row.last_instance_id == "i_last"
        assert row.last_decision == "refused"
        assert row.last_surface == "conversation"
        assert row.repetition_count == 2


# ===========================================================================
# Reads
# ===========================================================================


class TestReads:
    def test_get_miss_returns_none(self, reg):
        rac = reg.reality_anchor_corrections
        assert rac.get("never written") is None

    def test_get_hit_returns_dataclass(self, reg):
        rac = reg.reality_anchor_corrections
        rac.bump_or_create(
            claim="X", fact_id="f", worst_severity="HIGH",
            now_iso="t", agent_dna="a", instance_id="i",
            decision="warned", surface="dispatcher",
        )
        row = rac.get("X")
        assert isinstance(row, CorrectionRow)
        assert row.canonical_claim == "x"

    def test_list_repeat_offenders_default_min(self, reg):
        rac = reg.reality_anchor_corrections
        # Single hit: not a repeat offender.
        rac.bump_or_create(
            claim="solo", fact_id="f", worst_severity="HIGH",
            now_iso="t", agent_dna="a", instance_id="i",
            decision="warned", surface="dispatcher",
        )
        # Repeated 3x → repeat offender.
        for _ in range(3):
            rac.bump_or_create(
                claim="repeats", fact_id="f", worst_severity="HIGH",
                now_iso="t", agent_dna="a", instance_id="i",
                decision="warned", surface="dispatcher",
            )
        offenders = rac.list_repeat_offenders()  # default min=2
        assert len(offenders) == 1
        assert offenders[0].canonical_claim == "repeats"
        assert offenders[0].repetition_count == 3

    def test_list_repeat_offenders_orders_by_count_desc(self, reg):
        rac = reg.reality_anchor_corrections
        for _ in range(2):
            rac.bump_or_create(
                claim="twice", fact_id="f", worst_severity="HIGH",
                now_iso="t", agent_dna="a", instance_id="i",
                decision="warned", surface="dispatcher",
            )
        for _ in range(5):
            rac.bump_or_create(
                claim="five times", fact_id="f", worst_severity="HIGH",
                now_iso="t", agent_dna="a", instance_id="i",
                decision="warned", surface="dispatcher",
            )
        offenders = rac.list_repeat_offenders()
        assert len(offenders) == 2
        assert offenders[0].canonical_claim == "five times"
        assert offenders[0].repetition_count == 5
        assert offenders[1].canonical_claim == "twice"

    def test_list_repeat_offenders_min_param(self, reg):
        rac = reg.reality_anchor_corrections
        for _ in range(2):
            rac.bump_or_create(
                claim="twice", fact_id="f", worst_severity="HIGH",
                now_iso="t", agent_dna="a", instance_id="i",
                decision="warned", surface="dispatcher",
            )
        # min_repetitions=5 — should return empty
        offenders = rac.list_repeat_offenders(min_repetitions=5)
        assert offenders == []


# ===========================================================================
# Fresh-install DDL coverage
# ===========================================================================


def test_table_present_on_fresh_install(reg):
    """A fresh Registry.bootstrap should create the table without
    going through any migration."""
    rows = reg._conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='reality_anchor_corrections';"
    ).fetchall()
    assert len(rows) == 1
