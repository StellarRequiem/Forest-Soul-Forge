"""Unit tests for the Y7 retention-sweep router helpers.

The router itself (``POST /admin/conversations/sweep_retention``) is best
exercised end-to-end — it composes registry calls, dispatcher dispatch,
and audit-chain emits across a write-locked block. Unit-level scope is
the pure helpers: timestamp formatting, age-day arithmetic, and the
summary-prompt builder. The full HTTP path is exercised by
``live-test-y-full.command`` and gets v0.3 integration test coverage.

Phase A audit 2026-04-30 finding T-2 (router has zero direct tests).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from forest_soul_forge.daemon.routers.conversations_admin import (
    _age_days,
    _build_summary_prompt,
    _utc_now_iso,
)


# ===========================================================================
# _utc_now_iso — wall-clock formatter
# ===========================================================================
class TestUtcNowIso:
    def test_format_matches_contract(self):
        """ADR-0005 audit chain timestamps use ISO-8601 UTC with second
        precision and trailing Z. Drift from this format breaks the
        sweep's age calculation downstream."""
        s = _utc_now_iso()
        # Shape: YYYY-MM-DDTHH:MM:SSZ
        assert len(s) == 20
        assert s[4] == "-"
        assert s[7] == "-"
        assert s[10] == "T"
        assert s[13] == ":"
        assert s[16] == ":"
        assert s.endswith("Z")
        # Round-trips through strptime (the same parser used downstream).
        datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")

    def test_returns_utc_aware_value(self):
        """The string represents UTC. We verify by parsing and comparing
        to the current UTC time within a 5-second window."""
        s = _utc_now_iso()
        parsed = datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        assert abs((now - parsed).total_seconds()) < 5


# ===========================================================================
# _age_days — day-delta between two ISO timestamps
# ===========================================================================
class TestAgeDays:
    def test_same_timestamp_zero(self):
        s = "2026-04-30T12:00:00Z"
        assert _age_days(s, s) == 0.0

    def test_one_day_difference(self):
        now = "2026-04-30T12:00:00Z"
        turn = "2026-04-29T12:00:00Z"
        assert _age_days(now, turn) == 1.0

    def test_one_hour_difference(self):
        now = "2026-04-30T13:00:00Z"
        turn = "2026-04-30T12:00:00Z"
        # 1 hour = 1/24 day.
        result = _age_days(now, turn)
        assert abs(result - (1 / 24)) < 1e-9

    def test_seven_day_difference(self):
        now = "2026-04-30T00:00:00Z"
        turn = "2026-04-23T00:00:00Z"
        assert _age_days(now, turn) == 7.0

    def test_thirty_day_difference(self):
        now = "2026-04-30T00:00:00Z"
        turn = "2026-03-31T00:00:00Z"
        assert _age_days(now, turn) == 30.0

    def test_negative_age_when_turn_in_future(self):
        """A turn timestamp in the future relative to ``now`` produces
        a negative age. The sweep should never see this in practice
        (turns are written with _utc_now_iso AT write time), but the
        helper should not crash."""
        now = "2026-04-30T12:00:00Z"
        turn = "2026-05-01T12:00:00Z"
        result = _age_days(now, turn)
        assert result == -1.0

    def test_malformed_now_returns_zero(self):
        """Per the helper's contract: ``Returns 0.0 on parse failure
        (better to skip than crash the sweep)``. The sweep continues
        but the candidate is treated as age-zero (not yet ripe)."""
        assert _age_days("garbage", "2026-04-30T12:00:00Z") == 0.0

    def test_malformed_turn_returns_zero(self):
        assert _age_days("2026-04-30T12:00:00Z", "garbage") == 0.0

    def test_both_malformed_returns_zero(self):
        assert _age_days("garbage1", "garbage2") == 0.0

    def test_iso_with_microseconds_treated_as_malformed(self):
        """The strptime format used here is %Y-%m-%dT%H:%M:%SZ — no
        sub-second precision. A timestamp with microseconds (which
        the registry doesn't currently produce) would currently parse
        as malformed and return 0.0. Document this explicitly so a
        future change to either side surfaces here."""
        result = _age_days(
            "2026-04-30T12:00:00.123Z",  # microsecond suffix
            "2026-04-30T12:00:00Z",
        )
        assert result == 0.0  # parse failure → 0


# ===========================================================================
# _build_summary_prompt — summarizer instruction template
# ===========================================================================
class TestBuildSummaryPrompt:
    def test_includes_speaker_label(self):
        p = _build_summary_prompt(speaker_label="Atlas", body="some content")
        assert "Speaker: Atlas" in p

    def test_includes_body_verbatim(self):
        body = "the body of the original turn that gets summarized"
        p = _build_summary_prompt(speaker_label="anyone", body=body)
        assert body in p

    def test_emphasizes_purge(self):
        """The summarizer needs to know the body will be DESTROYED so
        it knows the summary must stand alone — that's load-bearing
        guidance, not optional flavor."""
        p = _build_summary_prompt(speaker_label="Atlas", body="x")
        assert "PURGED" in p or "purge" in p.lower()

    def test_caps_summary_length_in_instruction(self):
        p = _build_summary_prompt(speaker_label="Atlas", body="x")
        # Should ask for 1-2 sentences (or similar terse cap)
        assert "1-2 sentence" in p or "1 to 2 sentence" in p

    def test_anti_invention_clause(self):
        """Summarization MUST forbid invention — the summary replaces
        an audit-trailed body and operators rely on its accuracy."""
        p = _build_summary_prompt(speaker_label="Atlas", body="x")
        assert "Do NOT invent" in p or "do not invent" in p.lower()

    def test_separator_around_body(self):
        """The body is fenced with ``---`` markers so the model can
        reliably distinguish content from the surrounding instructions."""
        p = _build_summary_prompt(
            speaker_label="Forge",
            body="ambiguous content with: special chars",
        )
        assert "---" in p
        # Both opening and closing fences present.
        assert p.count("---") >= 2

    def test_empty_body_doesnt_crash(self):
        """Empty body (e.g. a turn that was already purged once) still
        produces a syntactically valid prompt; the model will return a
        terse 'empty input' summary that the caller can interpret."""
        p = _build_summary_prompt(speaker_label="anyone", body="")
        assert "Speaker: anyone" in p
        assert isinstance(p, str)
        assert len(p) > 0
