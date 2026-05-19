"""Unit tests for ADR-0083 lifecycle-aware idempotency replay.

The replay path at writes/_shared.py:_maybe_replay_cached gained an
opt-in ``is_still_valid`` callback in Burst 426. This file pins the
three cases that matter:

  1. Without the callback: replay behaves exactly as before
     (backward compatibility).
  2. With the callback returning True: replay proceeds normally
     (within-lifecycle retries work).
  3. With the callback returning False: replay returns None
     (cross-lifecycle replays — e.g. cached agent now archived —
     produce a cache miss so the request can process fresh).

Phase A (2026-04-30) conftest convention: seed_stub_agent for any
test that touches the agents table — FK enforcement is on.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from forest_soul_forge.daemon.routers.writes._shared import _maybe_replay_cached
from forest_soul_forge.registry import Registry


@pytest.fixture
def fresh_registry(tmp_path: Path) -> Registry:
    """A fresh registry bootstrapped to a temp file."""
    db_path = tmp_path / "test_registry.sqlite"
    return Registry.bootstrap(db_path)


def _seed_cache_entry(
    registry: Registry,
    *,
    key: str,
    endpoint: str,
    request_hash: str,
    status: int,
    body: dict,
) -> None:
    """Insert a row directly into idempotency_keys. Bypasses the
    public API (which couples to the writes router) — pure
    test-fixture utility.
    """
    registry._conn.execute(
        "INSERT INTO idempotency_keys (key, endpoint, request_hash, "
        "response_status, response_body, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            key,
            endpoint,
            request_hash,
            status,
            json.dumps(body),
            "2026-05-19T12:00:00Z",
        ),
    )
    registry._conn.commit()


def test_replay_without_validator_returns_cached_response(fresh_registry):
    """Backward-compatibility case: no validator → replay as before.

    This is the original Burst-77 contract. ADR-0083 must not
    change behavior for any caller that doesn't opt in.
    """
    _seed_cache_entry(
        fresh_registry,
        key="birth-X",
        endpoint="/birth",
        request_hash="hash-1",
        status=201,
        body={"instance_id": "role_abc_1", "status": "active"},
    )
    result = _maybe_replay_cached(
        fresh_registry, "birth-X", "/birth", "hash-1"
    )
    assert result is not None
    assert result.status_code == 201
    payload = json.loads(result.body)
    assert payload["instance_id"] == "role_abc_1"


def test_replay_with_valid_predicate_returns_cached_response(fresh_registry):
    """Validator returns True → replay proceeds (within-lifecycle
    retries continue working).
    """
    _seed_cache_entry(
        fresh_registry,
        key="birth-X",
        endpoint="/birth",
        request_hash="hash-1",
        status=201,
        body={"instance_id": "role_abc_1", "status": "active"},
    )
    result = _maybe_replay_cached(
        fresh_registry,
        "birth-X",
        "/birth",
        "hash-1",
        is_still_valid=lambda _: True,
    )
    assert result is not None
    assert result.status_code == 201


def test_replay_with_invalid_predicate_returns_none(fresh_registry):
    """Validator returns False → replay returns None (cache miss
    semantics). The caller then proceeds to process the request
    fresh. This is the ADR-0083 lifecycle-aware path.
    """
    _seed_cache_entry(
        fresh_registry,
        key="birth-X",
        endpoint="/birth",
        request_hash="hash-1",
        status=201,
        body={"instance_id": "role_abc_1", "status": "active"},
    )
    result = _maybe_replay_cached(
        fresh_registry,
        "birth-X",
        "/birth",
        "hash-1",
        is_still_valid=lambda _: False,
    )
    assert result is None, (
        "When the validator returns False, replay must return None "
        "so the caller can process the request fresh per ADR-0083."
    )


def test_replay_validator_receives_raw_body_bytes(fresh_registry):
    """Contract pin: the validator is invoked with the raw cached
    response body (the bytes the cache stored). It should be JSON-
    parseable by callers that need to inspect cached fields like
    instance_id.
    """
    captured = {}

    def _capture_and_pass(cached_json: bytes) -> bool:
        captured["bytes"] = cached_json
        parsed = json.loads(cached_json)
        captured["parsed"] = parsed
        return True

    _seed_cache_entry(
        fresh_registry,
        key="birth-X",
        endpoint="/birth",
        request_hash="hash-1",
        status=201,
        body={"instance_id": "role_abc_1", "status": "active"},
    )
    _maybe_replay_cached(
        fresh_registry,
        "birth-X",
        "/birth",
        "hash-1",
        is_still_valid=_capture_and_pass,
    )
    assert "bytes" in captured
    assert isinstance(captured["bytes"], (bytes, str))
    assert captured["parsed"]["instance_id"] == "role_abc_1"


def test_replay_no_key_returns_none(fresh_registry):
    """Sanity: missing idempotency key short-circuits before any
    validator invocation. Validator must not be called.
    """
    called = {"yes": False}

    def _should_not_be_called(_):
        called["yes"] = True
        return True

    result = _maybe_replay_cached(
        fresh_registry,
        None,
        "/birth",
        "hash-1",
        is_still_valid=_should_not_be_called,
    )
    assert result is None
    assert not called["yes"], (
        "Validator must not be invoked when the idempotency key is "
        "absent — there's no cache hit to validate."
    )


def test_replay_no_cache_hit_returns_none(fresh_registry):
    """Sanity: cache miss short-circuits before validator
    invocation. Validator must not be called.
    """
    called = {"yes": False}

    def _should_not_be_called(_):
        called["yes"] = True
        return True

    # No cache entry seeded.
    result = _maybe_replay_cached(
        fresh_registry,
        "birth-no-such-key",
        "/birth",
        "hash-1",
        is_still_valid=_should_not_be_called,
    )
    assert result is None
    assert not called["yes"], (
        "Validator must not be invoked on a true cache miss."
    )
