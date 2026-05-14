"""ADR-0067 T8 (B285) — /orchestrator/* endpoint tests.

Module-level smoke tests of the router-level helpers. The actual
endpoints get exercised in integration tests with a TestClient
(queued — same pattern as existing test_daemon_reality_anchor.py).

Tests here cover:
  - _domain_to_dict marshaling
  - _read_recent_routes filtering on domain_routed event_type
  - _count_recent_routes window filtering
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from forest_soul_forge.core.domain_registry import Domain, EntryAgent
from forest_soul_forge.daemon.routers.orchestrator import (
    _count_recent_routes,
    _domain_to_dict,
    _is_routing_event,
    _read_recent_routes,
)


class _FakeChain:
    """Minimal audit chain mock that returns canned entries."""
    def __init__(self, entries: list[Any]):
        self._entries = entries

    def tail(self, n: int) -> list[Any]:
        return list(reversed(self._entries[-n:]))


def _entry(seq: int, event_type: str, ts: str = None, event_data: dict = None):
    return SimpleNamespace(
        seq=seq,
        timestamp=ts or "2026-05-14T12:00:00Z",
        event_type=event_type,
        event_data=event_data or {},
        agent_dna="test-dna",
        entry_hash=f"hash-{seq}",
        prev_hash=f"prev-{seq}",
    )


# ---------------------------------------------------------------------------
# _domain_to_dict
# ---------------------------------------------------------------------------
def test_domain_to_dict_carries_all_fields():
    d = Domain(
        domain_id="d_x", name="X", status="live",
        description="x test",
        entry_agents=(EntryAgent("role_x", "cap_x"),),
        capabilities=("cap_x", "cap_y"),
        example_intents=("do x",),
        depends_on_substrate=("ADR-0001",),
        depends_on_connectors=("forest-x",),
        handoff_targets=("d_y",),
        notes="test notes",
    )
    out = _domain_to_dict(d)
    assert out["domain_id"] == "d_x"
    assert out["is_dispatchable"] is True
    assert out["entry_agents"] == [
        {"role": "role_x", "capability": "cap_x"},
    ]
    assert out["capabilities"] == ["cap_x", "cap_y"]
    assert out["handoff_targets"] == ["d_y"]


# ---------------------------------------------------------------------------
# _is_routing_event
# ---------------------------------------------------------------------------
def test_is_routing_event_matches_domain_routed():
    assert _is_routing_event("domain_routed") is True
    assert _is_routing_event("agent_delegated") is False
    assert _is_routing_event("tool_call_succeeded") is False


# ---------------------------------------------------------------------------
# _read_recent_routes
# ---------------------------------------------------------------------------
def test_read_recent_routes_filters_to_domain_routed():
    chain = _FakeChain([
        _entry(1, "tool_call_succeeded"),
        _entry(2, "domain_routed", event_data={"target_domain": "d_a"}),
        _entry(3, "agent_delegated"),
        _entry(4, "domain_routed", event_data={"target_domain": "d_b"}),
    ])
    routes = _read_recent_routes(chain, limit=10)
    assert len(routes) == 2
    assert all(r["event_type"] == "domain_routed" for r in routes)


def test_read_recent_routes_honors_limit():
    chain = _FakeChain([
        _entry(i, "domain_routed", event_data={"target_domain": "d_a"})
        for i in range(100)
    ])
    routes = _read_recent_routes(chain, limit=5)
    assert len(routes) == 5


def test_read_recent_routes_empty_chain():
    chain = _FakeChain([])
    routes = _read_recent_routes(chain, limit=10)
    assert routes == []


# ---------------------------------------------------------------------------
# _count_recent_routes
# ---------------------------------------------------------------------------
def test_count_recent_routes_window_filtering():
    """Routes outside the 24h window are excluded from totals."""
    now = datetime.now(timezone.utc)
    recent_ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    old_ts = (now - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    chain = _FakeChain([
        _entry(1, "domain_routed", ts=recent_ts,
               event_data={"target_domain": "d_a"}),
        _entry(2, "domain_routed", ts=recent_ts,
               event_data={"target_domain": "d_a"}),
        _entry(3, "domain_routed", ts=recent_ts,
               event_data={"target_domain": "d_b"}),
        _entry(4, "domain_routed", ts=old_ts,
               event_data={"target_domain": "d_a"}),  # outside window
    ])
    total, by_domain = _count_recent_routes(chain, window_hours=24)
    assert total == 3
    assert by_domain == {"d_a": 2, "d_b": 1}


def test_count_recent_routes_empty():
    chain = _FakeChain([])
    total, by_domain = _count_recent_routes(chain)
    assert total == 0
    assert by_domain == {}


def test_count_recent_routes_missing_timestamp_skipped():
    """Entries with no timestamp don't crash the counter; they're
    just excluded from the count."""
    chain = _FakeChain([
        _entry(1, "domain_routed", ts="",
               event_data={"target_domain": "d_a"}),
    ])
    # Override the timestamp directly to empty (passes through _entry's default)
    chain._entries[0].timestamp = ""
    total, by_domain = _count_recent_routes(chain)
    assert total == 0
