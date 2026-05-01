"""Unit tests for chronicle/render.py — operator-facing audit export.

ADR-003X K5. Coverage was 0 unit tests at the Phase A audit
(2026-04-30 finding T-5). The 562 LoC of presentation logic is a
real risk surface — sanitization-correctness directly determines
whether a chronicle leaks payload fields the operator didn't intend
to share.

Coverage strategy:
  - milestone classification (is_milestone)
  - per-event sanitizers — each one tested with the canonical input
    shape AND a malformed shape (missing fields)
  - sanitize_event() — fallback when the sanitizer raises, fallback
    when the event_type is unknown
  - filters — filter_by_dna, filter_by_bond_name (3 event types)
  - render_markdown — title, milestone star, payload include/exclude,
    sort order, HTML escaping in markdown is N/A
  - render_html — head/tail wrap, escape protection, milestone classes,
    warn classes, empty-state, include_payload toggle
  - _event_classes — milestone vs warn vs neither
"""
from __future__ import annotations

from forest_soul_forge.chronicle.render import (
    MILESTONE_EVENT_TYPES,
    SANITIZERS,
    _event_classes,
    _short,
    filter_by_bond_name,
    filter_by_dna,
    is_milestone,
    render_html,
    render_markdown,
    sanitize_event,
)
from forest_soul_forge.core.audit_chain import ChainEntry


def _entry(seq=1, event_type="agent_created", event_data=None,
           agent_dna="abc123def456", timestamp="2026-04-30T12:00:00Z") -> ChainEntry:
    return ChainEntry(
        seq=seq,
        timestamp=timestamp,
        prev_hash="prev",
        entry_hash="curr",
        agent_dna=agent_dna,
        event_type=event_type,
        event_data=event_data or {},
    )


# ===========================================================================
# _short — string truncator
# ===========================================================================
class TestShort:
    def test_none_returns_unknown(self):
        assert _short(None) == "<unknown>"
        assert _short("") == "<unknown>"

    def test_short_string_unchanged(self):
        assert _short("abc") == "abc"

    def test_truncates_at_default_16(self):
        s = "0123456789abcdef-extra"  # 22 chars
        assert _short(s) == "0123456789abcdef…"

    def test_custom_length(self):
        assert _short("abcdefgh", n=4) == "abcd…"


# ===========================================================================
# is_milestone — set membership
# ===========================================================================
class TestIsMilestone:
    def test_milestone_event(self):
        assert is_milestone(_entry(event_type="agent_created")) is True
        assert is_milestone(_entry(event_type="agent_archived")) is True
        assert is_milestone(_entry(event_type="ceremony")) is True
        assert is_milestone(_entry(event_type="memory_verified")) is True
        assert is_milestone(_entry(event_type="hardware_mismatch")) is True

    def test_non_milestone_event(self):
        assert is_milestone(_entry(event_type="tool_call_dispatched")) is False
        assert is_milestone(_entry(event_type="tool_call_succeeded")) is False
        assert is_milestone(_entry(event_type="skill_invoked")) is False

    def test_unknown_event_type_not_milestone(self):
        assert is_milestone(_entry(event_type="completely_made_up")) is False

    def test_milestone_set_is_frozen(self):
        """Symbolic check that the set is the immutable kind."""
        assert isinstance(MILESTONE_EVENT_TYPES, frozenset)


# ===========================================================================
# Per-event sanitizers — happy path
# ===========================================================================
class TestSanitizers:
    def test_chain_created(self):
        out = sanitize_event(_entry(event_type="chain_created"))
        assert out == "Chain genesis"

    def test_agent_created(self):
        out = sanitize_event(_entry(
            event_type="agent_created",
            event_data={"agent_name": "Atlas", "role": "system_architect"},
        ))
        assert "Atlas" in out
        assert "system_architect" in out

    def test_agent_archived_with_reason(self):
        out = sanitize_event(_entry(
            event_type="agent_archived",
            event_data={"instance_id": "i1-abcdef", "reason": "experimental"},
        ))
        assert "i1-abcdef" in out
        assert "experimental" in out

    def test_agent_archived_no_reason(self):
        out = sanitize_event(_entry(
            event_type="agent_archived",
            event_data={"instance_id": "i1"},
        ))
        assert "archived" in out

    def test_agent_delegated_basic(self):
        out = sanitize_event(_entry(
            event_type="agent_delegated",
            event_data={
                "caller_instance": "atlas-id",
                "target_instance": "forge-id",
                "skill_name": "compile_diff",
            },
        ))
        assert "atlas-id" in out
        assert "forge-id" in out
        assert "compile_diff" in out

    def test_agent_delegated_triune(self):
        out = sanitize_event(_entry(
            event_type="agent_delegated",
            event_data={
                "caller_instance": "atlas-id",
                "target_instance": "forge-id",
                "skill_name": "skill",
                "triune_internal": True,
                "triune_bond_name": "the-bond",
            },
        ))
        assert "the-bond" in out
        assert "triune" in out.lower()

    def test_agent_delegated_out_of_lineage(self):
        out = sanitize_event(_entry(
            event_type="agent_delegated",
            event_data={
                "caller_instance": "a",
                "target_instance": "b",
                "skill_name": "s",
                "allow_out_of_lineage": True,
            },
        ))
        assert "out-of-lineage" in out

    def test_ceremony(self):
        out = sanitize_event(_entry(
            event_type="ceremony",
            event_data={"ceremony_name": "bond", "summary": "three minds, one purpose"},
        ))
        assert "bond" in out
        assert "three minds, one purpose" in out

    def test_memory_verified(self):
        out = sanitize_event(_entry(
            event_type="memory_verified",
            event_data={"instance_id": "i1", "entry_id": "e42", "verifier_id": "alex"},
        ))
        assert "e42" in out
        assert "alex" in out

    def test_out_of_triune_attempt_warns(self):
        out = sanitize_event(_entry(
            event_type="out_of_triune_attempt",
            event_data={"caller_instance": "x", "target_instance": "y", "bond_name": "b"},
        ))
        assert "⚠" in out
        assert "out-of-bond" in out

    def test_governance_relaxed_lineage_bypass(self):
        out = sanitize_event(_entry(
            event_type="governance_relaxed",
            event_data={
                "relaxation_type": "out_of_lineage_delegate",
                "caller_instance": "a", "target_instance": "b",
                "skill_name": "skill",
            },
        ))
        assert "⚠" in out
        assert "lineage" in out

    def test_governance_relaxed_spawn_override(self):
        out = sanitize_event(_entry(
            event_type="governance_relaxed",
            event_data={
                "relaxation_type": "spawn_genre_override",
                "instance_id": "i1",
                "parent_genre": "observer",
                "child_genre": "actuator",
            },
        ))
        assert "observer" in out
        assert "actuator" in out

    def test_hardware_mismatch_includes_short_fingerprints(self):
        out = sanitize_event(_entry(
            event_type="hardware_mismatch",
            event_data={
                "instance_id": "i1",
                "tool_key": "timestamp_window.v1",
                "expected_machine_fingerprint": "abc12345xxxx",
                "constitution_binding": "def67890yyyy",
            },
        ))
        assert "⚠" in out
        # Both first-8 chars should appear
        assert "abc12345" in out
        assert "def67890" in out

    def test_secret_set_no_value_leak(self):
        """Secret-set events name the secret but never include the
        value. The renderer must not surface anything from event_data
        that isn't whitelisted."""
        out = sanitize_event(_entry(
            event_type="secret_set",
            event_data={
                "instance_id": "i1",
                "name": "GITHUB_TOKEN",
                "value": "ghp_THIS_SHOULD_NEVER_BE_RENDERED",  # not in chain anyway
            },
        ))
        assert "GITHUB_TOKEN" in out
        assert "ghp_THIS_SHOULD_NEVER_BE_RENDERED" not in out

    def test_task_caps_set_with_caps(self):
        out = sanitize_event(_entry(
            event_type="task_caps_set",
            event_data={
                "session_id": "sess-1",
                "context_cap_tokens": 8000,
                "usage_cap_tokens": 50000,
            },
        ))
        assert "8000" in out
        assert "50000" in out

    def test_tool_dispatched(self):
        out = sanitize_event(_entry(
            event_type="tool_call_dispatched",
            event_data={"instance_id": "i1", "tool_key": "memory_write.v1"},
        ))
        assert "memory_write.v1" in out

    def test_tool_failed_with_message(self):
        out = sanitize_event(_entry(
            event_type="tool_call_failed",
            event_data={
                "tool_key": "memory_write.v1",
                "exception_message": "FK constraint violated",
            },
        ))
        assert "memory_write.v1" in out
        assert "FK constraint violated" in out


# ===========================================================================
# sanitize_event fallback paths
# ===========================================================================
class TestSanitizeEventFallback:
    def test_unknown_event_type_returns_type_only(self):
        out = sanitize_event(_entry(event_type="newly_invented_event"))
        assert out == "(newly_invented_event)"

    def test_malformed_payload_falls_back_to_type(self):
        """If a sanitizer raises (e.g. payload missing a required key
        in a way the sanitizer doesn't guard), fallback to the type
        name. Crucial — a malformed payload must NEVER break the renderer."""

        # Inject a sanitizer that always raises:
        def _broken(_d):
            raise RuntimeError("sanitizer broken")
        original = SANITIZERS.get("chain_created")
        SANITIZERS["chain_created"] = _broken
        try:
            out = sanitize_event(_entry(event_type="chain_created"))
            assert out == "(chain_created)"
        finally:
            SANITIZERS["chain_created"] = original

    def test_empty_event_data_doesnt_crash(self):
        # Many sanitizers ``d.get(...)`` with defaults — should be safe.
        for et in [
            "agent_created", "agent_archived", "agent_delegated",
            "memory_verified", "secret_set", "tool_call_dispatched",
            "tool_call_failed",
        ]:
            out = sanitize_event(_entry(event_type=et, event_data={}))
            assert isinstance(out, str)
            assert out  # non-empty


# ===========================================================================
# Filters
# ===========================================================================
class TestFilters:
    def test_filter_by_dna_matches(self):
        e1 = _entry(seq=1, agent_dna="aaa111")
        e2 = _entry(seq=2, agent_dna="bbb222")
        e3 = _entry(seq=3, agent_dna="aaa111")
        out = filter_by_dna([e1, e2, e3], "aaa111")
        assert [e.seq for e in out] == [1, 3]

    def test_filter_by_dna_no_match(self):
        e1 = _entry(agent_dna="aaa111")
        assert filter_by_dna([e1], "zzz999") == []

    def test_filter_by_dna_empty(self):
        assert filter_by_dna([], "anything") == []

    def test_filter_by_bond_name_ceremony(self):
        e = _entry(
            seq=10, event_type="ceremony",
            event_data={"bond_name": "the-bond", "summary": "x"},
        )
        out = filter_by_bond_name([e], "the-bond")
        assert [x.seq for x in out] == [10]

    def test_filter_by_bond_name_delegated_internal(self):
        e = _entry(
            seq=11, event_type="agent_delegated",
            event_data={"triune_bond_name": "the-bond", "skill_name": "x"},
        )
        out = filter_by_bond_name([e], "the-bond")
        assert [x.seq for x in out] == [11]

    def test_filter_by_bond_name_out_of_triune(self):
        e = _entry(
            seq=12, event_type="out_of_triune_attempt",
            event_data={"bond_name": "the-bond"},
        )
        out = filter_by_bond_name([e], "the-bond")
        assert [x.seq for x in out] == [12]

    def test_filter_by_bond_name_other_bond_excluded(self):
        e = _entry(seq=13, event_type="ceremony", event_data={"bond_name": "different"})
        assert filter_by_bond_name([e], "the-bond") == []

    def test_filter_by_bond_name_unrelated_event_excluded(self):
        e = _entry(seq=14, event_type="agent_created")
        assert filter_by_bond_name([e], "the-bond") == []


# ===========================================================================
# _event_classes — milestone vs warn vs neither
# ===========================================================================
class TestEventClasses:
    def test_milestone_class(self):
        out = _event_classes(_entry(event_type="agent_created"))
        assert "milestone" in out
        assert "event" in out

    def test_warn_class_for_failed_dispatch(self):
        out = _event_classes(_entry(event_type="tool_call_failed"))
        assert "warn" in out

    def test_warn_and_milestone_both_for_hardware_mismatch(self):
        out = _event_classes(_entry(event_type="hardware_mismatch"))
        assert "milestone" in out
        assert "warn" in out

    def test_neither_class_for_routine(self):
        out = _event_classes(_entry(event_type="tool_call_dispatched"))
        # Just "event" — neither milestone nor warn
        assert "milestone" not in out
        assert "warn" not in out


# ===========================================================================
# render_markdown
# ===========================================================================
class TestRenderMarkdown:
    def test_includes_title(self):
        md = render_markdown([], title="Test Chronicle")
        assert "# Test Chronicle" in md

    def test_empty_entries_renders(self):
        md = render_markdown([], title="X")
        assert "Events: 0" in md
        assert "Milestones: 0" in md

    def test_milestone_marked_with_star(self):
        e = _entry(event_type="agent_created", event_data={"agent_name": "A", "role": "r"})
        md = render_markdown([e], title="X")
        assert "★" in md

    def test_non_milestone_no_star(self):
        e = _entry(event_type="tool_call_dispatched", event_data={"tool_key": "t.v1"})
        md = render_markdown([e], title="X")
        # Should have the routine list-marker " " not "★"
        assert "★" not in md

    def test_payload_excluded_by_default(self):
        e = _entry(
            event_type="agent_created",
            event_data={"agent_name": "A", "role": "r", "secret_field": "DO_NOT_LEAK"},
        )
        md = render_markdown([e], title="X")
        assert "DO_NOT_LEAK" not in md

    def test_payload_included_when_requested(self):
        e = _entry(
            event_type="agent_created",
            event_data={"agent_name": "A", "role": "r"},
        )
        md = render_markdown([e], title="X", include_payload=True)
        assert '"agent_name"' in md
        assert "```json" in md

    def test_sort_default_ascending(self):
        e1 = _entry(seq=1, event_type="chain_created")
        e2 = _entry(seq=2, event_type="agent_created")
        md = render_markdown([e2, e1], title="X")
        # seq 1 row comes before seq 2 row
        idx1 = md.index("seq 1")
        idx2 = md.index("seq 2")
        assert idx1 < idx2

    def test_sort_reverse(self):
        e1 = _entry(seq=1, event_type="chain_created")
        e2 = _entry(seq=2, event_type="agent_created", event_data={"agent_name": "X", "role": "r"})
        md = render_markdown([e1, e2], title="X", sort_reverse=True)
        idx1 = md.index("seq 1")
        idx2 = md.index("seq 2")
        assert idx2 < idx1


# ===========================================================================
# render_html
# ===========================================================================
class TestRenderHtml:
    def test_single_file_self_contained(self):
        html_out = render_html([], title="T")
        assert "<!doctype html>" in html_out
        assert "</html>" in html_out
        # Inline CSS, no external assets
        assert "<style>" in html_out
        assert "<link " not in html_out

    def test_title_html_escaped(self):
        """HTML injection in titles must be escaped."""
        html_out = render_html([], title="<script>alert(1)</script>")
        # The literal tag should not appear unescaped
        assert "<script>alert(1)</script>" not in html_out
        # Escaped form should appear
        assert "&lt;script&gt;" in html_out

    def test_subtitle_rendered_when_supplied(self):
        html_out = render_html([], title="T", subtitle="some context")
        assert "some context" in html_out

    def test_empty_state_message(self):
        html_out = render_html([], title="T")
        assert "No events to render" in html_out

    def test_milestone_class_applied(self):
        e = _entry(event_type="agent_created", event_data={"agent_name": "A", "role": "r"})
        html_out = render_html([e], title="T")
        assert "milestone" in html_out

    def test_warn_class_applied(self):
        e = _entry(event_type="tool_call_failed", event_data={"tool_key": "t.v1"})
        html_out = render_html([e], title="T")
        assert "warn" in html_out

    def test_payload_omitted_by_default(self):
        e = _entry(
            event_type="agent_created",
            event_data={"agent_name": "A", "role": "r", "secret": "NEVER_LEAK"},
        )
        html_out = render_html([e], title="T")
        assert "NEVER_LEAK" not in html_out
        # Footer notes payload is omitted
        assert "payload omitted by default" in html_out

    def test_payload_included_when_requested(self):
        e = _entry(
            event_type="agent_created",
            event_data={"agent_name": "A", "role": "r"},
        )
        html_out = render_html([e], title="T", include_payload=True)
        assert "raw payload" in html_out
        # JSON payload is HTML-escaped before it lands in the <pre> block,
        # so quotes appear as &quot;. Either form proves the payload made
        # it into the output — both protections are working as designed.
        assert "agent_name" in html_out
        assert "&quot;" in html_out

    def test_milestone_toggle_javascript_present(self):
        """The single embedded script handles the milestones-only toggle."""
        html_out = render_html([], title="T")
        assert "milestonesOnly" in html_out

    def test_event_data_html_escaped_in_summary(self):
        """User-supplied event_data fields that bubble up to the summary
        must be escaped before rendering."""
        e = _entry(
            event_type="agent_created",
            event_data={"agent_name": "<img src=x onerror=alert(1)>", "role": "r"},
        )
        html_out = render_html([e], title="T")
        # Tag should not appear literally (rendered as text)
        assert "<img src=x" not in html_out
        # Escaped form should appear
        assert "&lt;img src=x" in html_out

    def test_stats_count_correct(self):
        events = [
            _entry(seq=1, event_type="chain_created"),
            _entry(seq=2, event_type="agent_created", event_data={"agent_name": "A", "role": "r"}),
            _entry(seq=3, event_type="tool_call_dispatched", event_data={"tool_key": "t.v1"}),
        ]
        html_out = render_html(events, title="T")
        # 3 total, 2 milestones (chain_created + agent_created)
        assert "Events <b>3</b>" in html_out
        assert "Milestones <b>2</b>" in html_out
