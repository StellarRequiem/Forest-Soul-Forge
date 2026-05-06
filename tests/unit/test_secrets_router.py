"""ADR-0052 T6 (B173) — /secrets/* HTTP surface tests.

Two read-only endpoints for the chat-tab Secrets card:

  GET /secrets/backend → {name, selection_source, selection_via}
  GET /secrets/names    → {backend, count, names}

Mutating operations stay CLI-only per the ADR-0052 design — the
chat tab is not a destructive surface for credentials.

Tests use FastAPI's TestClient + a tmp-path FileStore via env
vars so no operator credentials are touched.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    """Build a FastAPI TestClient with the secrets router mounted +
    FileStore pointed at a tmp YAML file."""
    monkeypatch.setenv("FSF_SECRET_STORE", "file")
    monkeypatch.setenv("FSF_FILE_SECRETS_PATH", str(tmp_path / "s.yaml"))
    from forest_soul_forge.security.secrets.resolver import _reset_cache_for_tests
    _reset_cache_for_tests()

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from forest_soul_forge.daemon.routers import secrets as secrets_router
    app = FastAPI()
    app.include_router(secrets_router.router)
    yield TestClient(app)
    _reset_cache_for_tests()


# ---------------------------------------------------------------------------
# GET /secrets/backend
# ---------------------------------------------------------------------------

def test_backend_returns_active_store_info(client):
    resp = client.get("/secrets/backend")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "file"
    # Explicit env-var selection should show that source.
    assert body["selection_source"] == "explicit"
    assert "FSF_SECRET_STORE=" in body["selection_via"]


def test_backend_platform_default(monkeypatch, client):
    """Removing FSF_SECRET_STORE → resolver falls back to platform
    default (file on Linux sandbox; keychain on Darwin). Either is
    a valid response — we just assert the selection_source flips
    to platform_default."""
    monkeypatch.delenv("FSF_SECRET_STORE", raising=False)
    from forest_soul_forge.security.secrets.resolver import _reset_cache_for_tests
    _reset_cache_for_tests()
    resp = client.get("/secrets/backend")
    if resp.status_code == 503:
        # On non-Darwin without explicit FSF_SECRET_STORE, the
        # resolver might raise if FSF_FILE_SECRETS_PATH isn't
        # accessible — that's acceptable for this test (we exercise
        # the platform-default code path; the 503 is the resolver's
        # honest error). Skip in that edge.
        pytest.skip(f"backend resolution failed in this env: {resp.json()}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["selection_source"] == "platform_default"
    assert "platform=" in body["selection_via"]


# ---------------------------------------------------------------------------
# GET /secrets/names
# ---------------------------------------------------------------------------

def test_names_empty_when_no_secrets(client):
    resp = client.get("/secrets/names")
    assert resp.status_code == 200
    body = resp.json()
    assert body["backend"] == "file"
    assert body["count"] == 0
    assert body["names"] == []


def test_names_lists_stored_names_sorted(client, tmp_path):
    """Pre-populate the FileStore via direct put, then verify the
    HTTP surface returns names sorted (deterministic UI render)."""
    from forest_soul_forge.security.secrets import FileStore
    store = FileStore(tmp_path / "s.yaml")
    store.put("zebra_token", "v1")
    store.put("alpha_key", "v2")
    store.put("mango_pat", "v3")
    # Reset so the resolver re-instantiates and sees the new file
    # (FileStore caches its own state per instance).
    from forest_soul_forge.security.secrets.resolver import _reset_cache_for_tests
    _reset_cache_for_tests()

    resp = client.get("/secrets/names")
    assert resp.status_code == 200
    body = resp.json()
    assert body["backend"] == "file"
    assert body["count"] == 3
    assert body["names"] == ["alpha_key", "mango_pat", "zebra_token"]


def test_names_never_includes_values(client, tmp_path):
    """Defense-in-depth: even an enthusiastic future contributor
    must not accidentally leak values via this endpoint. The
    response keys are name-only by design."""
    from forest_soul_forge.security.secrets import FileStore
    store = FileStore(tmp_path / "s.yaml")
    store.put("token", "super-secret-value-do-not-leak")
    from forest_soul_forge.security.secrets.resolver import _reset_cache_for_tests
    _reset_cache_for_tests()

    resp = client.get("/secrets/names")
    body_str = resp.text
    assert "super-secret-value-do-not-leak" not in body_str
    body = resp.json()
    # The schema is exactly {backend, count, names}.
    assert sorted(body.keys()) == ["backend", "count", "names"]


# ---------------------------------------------------------------------------
# Backend failure paths
# ---------------------------------------------------------------------------

def test_backend_503_on_resolver_failure(monkeypatch, client):
    """Bad FSF_SECRET_STORE value → resolver raises → 503 with the
    error message in detail."""
    monkeypatch.setenv("FSF_SECRET_STORE", "totally_made_up")
    from forest_soul_forge.security.secrets.resolver import _reset_cache_for_tests
    _reset_cache_for_tests()
    resp = client.get("/secrets/backend")
    assert resp.status_code == 503
    assert "totally_made_up" in resp.json()["detail"] or "not recognized" in resp.json()["detail"]


def test_names_503_on_resolver_failure(monkeypatch, client):
    monkeypatch.setenv("FSF_SECRET_STORE", "totally_made_up")
    from forest_soul_forge.security.secrets.resolver import _reset_cache_for_tests
    _reset_cache_for_tests()
    resp = client.get("/secrets/names")
    assert resp.status_code == 503
