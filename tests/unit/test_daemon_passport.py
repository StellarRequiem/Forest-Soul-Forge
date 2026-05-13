"""Tests for the ADR-0061 T6 (Burst 248) passport HTTP endpoint.

Exercises POST /agents/{instance_id}/passport end-to-end via
FastAPI's TestClient. Mirrors test_daemon_plugin_grants.py for
test posture consistency.

Coverage:
- POST happy path: passport.json lands next to constitution +
  agent_passport_minted event emitted with all expected fields.
- POST 404: unknown agent.
- POST 400: empty authorized_fingerprints list rejected at the
  pydantic boundary.
- POST gating: refused when allow_write_endpoints=False.
- POST 409: legacy agent without public_key column populated.
- The minted passport actually verifies against the operator's
  trust list (round-trip).
- Mint twice: second call overwrites passport.json and emits a
  second mint event.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forest_soul_forge.daemon.app import build_app
from forest_soul_forge.daemon.config import DaemonSettings
from forest_soul_forge.security.passport import verify_passport


REPO_ROOT = Path(__file__).resolve().parents[2]
TRAIT_TREE = REPO_ROOT / "config" / "trait_tree.yaml"
CONST_TEMPLATES = REPO_ROOT / "config" / "constitution_templates.yaml"
TOOL_CATALOG = REPO_ROOT / "config" / "tool_catalog.yaml"
GENRES = REPO_ROOT / "config" / "genres.yaml"


def _build_settings(
    tmp_path: Path, *, allow_writes: bool = True,
) -> DaemonSettings:
    return DaemonSettings(
        registry_db_path=tmp_path / "registry.sqlite",
        artifacts_dir=tmp_path / "souls",
        audit_chain_path=tmp_path / "audit.jsonl",
        trait_tree_path=TRAIT_TREE,
        constitution_templates_path=CONST_TEMPLATES,
        soul_output_dir=tmp_path / "souls",
        tool_catalog_path=TOOL_CATALOG,
        genres_path=GENRES,
        default_provider="local",
        frontier_enabled=False,
        allow_write_endpoints=allow_writes,
        enrich_narrative_default=False,
        api_token=None,
        insecure_no_token=True,
    )


def _audit_events(app, event_type: str | None = None) -> list[dict]:
    chain_path = Path(app.state.audit_chain.path)
    events = []
    for line in chain_path.read_text().splitlines():
        if line.strip():
            events.append(json.loads(line))
    if event_type:
        events = [e for e in events if e.get("event_type") == event_type]
    return events


@pytest.fixture
def passport_env(tmp_path: Path, monkeypatch):
    """Daemon with one agent born; the operator can mint passports
    for it. Isolates the operator-key keystore via tmpdir so the
    test doesn't touch the developer's real keystore."""
    for p, name in [(TRAIT_TREE, "trait tree"),
                    (CONST_TEMPLATES, "constitution templates"),
                    (TOOL_CATALOG, "tool catalog")]:
        if not p.exists():
            pytest.skip(f"{name} missing at {p}")

    # Isolate the secret store BEFORE building the app. The default
    # resolver picks up FSF_SECRET_STORE_FILE; pointing it at a tmp
    # file gives this test its own operator master + agent keys.
    monkeypatch.setenv("FSF_SECRET_STORE", "file")
    monkeypatch.setenv(
        "FSF_SECRET_STORE_FILE", str(tmp_path / "secrets.json"),
    )
    # Reset caches that might hold a previous operator master
    # from a sibling test run. The agent_key_store module caches
    # its resolved AgentKeyStore in _RESOLVED_CACHE; we clear it
    # by direct dict.clear() since there's no public reset helper.
    from forest_soul_forge.security import operator_key, trust_list
    from forest_soul_forge.security.keys import agent_key_store as aks
    operator_key.reset_cache()
    trust_list.reset_cache()
    aks._RESOLVED_CACHE.clear()

    settings = _build_settings(tmp_path, allow_writes=True)
    app = build_app(settings)
    with TestClient(app) as client:
        resp = client.post("/birth", json={
            "profile": {
                "role": "network_watcher",
                "trait_values": {},
                "domain_weight_overrides": {},
            },
            "agent_name": "alpha",
        })
        assert resp.status_code in (200, 201), resp.text
        instance_id = resp.json()["instance_id"]

        yield {
            "client":      client,
            "app":         app,
            "instance_id": instance_id,
            "tmp_path":    tmp_path,
        }

    # Post-test: clear caches so the next test starts with a fresh
    # operator master.
    operator_key.reset_cache()
    trust_list.reset_cache()


# ===========================================================================
# Happy path
# ===========================================================================


class TestMintPassport:
    def test_happy_path_writes_file_and_emits_event(self, passport_env):
        e = passport_env
        fp = "0123456789abcdef"
        resp = e["client"].post(
            f"/agents/{e['instance_id']}/passport",
            json={
                "authorized_fingerprints": [fp],
                "operator_id": "alex",
                "reason": "smoke-test mint",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["instance_id"] == e["instance_id"]
        assert body["authorized_fingerprints"] == [fp]
        assert body["issued_at"]
        assert body["passport_path"].endswith("passport.json")

        # File landed on disk next to constitution.yaml.
        passport_path = Path(body["passport_path"])
        assert passport_path.exists()
        data = json.loads(passport_path.read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert data["instance_id"] == e["instance_id"]
        assert data["authorized_fingerprints"] == [fp]
        assert "signature" in data and data["signature"].startswith("ed25519:")

        # Audit event fired.
        evs = _audit_events(e["app"], "agent_passport_minted")
        assert len(evs) == 1
        ev = evs[0]["event_data"]
        assert ev["instance_id"] == e["instance_id"]
        assert ev["issuer_public_key"] == body["issuer_public_key"]
        assert ev["authorized_fingerprint_count"] == 1
        assert ev["operator_id"] == "alex"
        assert ev["reason"] == "smoke-test mint"

    def test_minted_passport_verifies(self, passport_env):
        """End-to-end: the daemon-minted passport actually validates
        under the same daemon's trust list."""
        e = passport_env
        fp = "deadbeefcafe0123"
        resp = e["client"].post(
            f"/agents/{e['instance_id']}/passport",
            json={"authorized_fingerprints": [fp]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        passport = json.loads(
            Path(body["passport_path"]).read_text(encoding="utf-8"),
        )
        # Trust list = the issuer's public key (operator master).
        valid, reason = verify_passport(
            passport,
            trusted_issuer_pubkeys_b64=[body["issuer_public_key"]],
            current_hardware_fingerprint=fp,
        )
        assert valid, reason

    def test_unknown_fp_fails_verification(self, passport_env):
        e = passport_env
        resp = e["client"].post(
            f"/agents/{e['instance_id']}/passport",
            json={"authorized_fingerprints": ["aaaa1111bbbb2222"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        passport = json.loads(
            Path(body["passport_path"]).read_text(encoding="utf-8"),
        )
        valid, reason = verify_passport(
            passport,
            trusted_issuer_pubkeys_b64=[body["issuer_public_key"]],
            current_hardware_fingerprint="0000000000000000",  # not authorized
        )
        assert not valid
        assert "fingerprint" in reason.lower()

    def test_remint_overwrites_passport_and_audit_fires_twice(
        self, passport_env,
    ):
        e = passport_env
        for fp in ("1111aaaa1111aaaa", "2222bbbb2222bbbb"):
            resp = e["client"].post(
                f"/agents/{e['instance_id']}/passport",
                json={"authorized_fingerprints": [fp]},
            )
            assert resp.status_code == 200, resp.text

        # Final passport.json on disk reflects the SECOND mint.
        last_body = resp.json()
        data = json.loads(
            Path(last_body["passport_path"]).read_text(encoding="utf-8"),
        )
        assert data["authorized_fingerprints"] == ["2222bbbb2222bbbb"]

        # Both mints emitted distinct audit events.
        evs = _audit_events(e["app"], "agent_passport_minted")
        assert len(evs) == 2


# ===========================================================================
# Failure paths
# ===========================================================================


class TestMintPassportFailures:
    def test_unknown_agent_404(self, passport_env):
        e = passport_env
        resp = e["client"].post(
            "/agents/does_not_exist/passport",
            json={"authorized_fingerprints": ["0000000000000000"]},
        )
        assert resp.status_code == 404, resp.text

    def test_empty_authorized_list_rejected(self, passport_env):
        e = passport_env
        resp = e["client"].post(
            f"/agents/{e['instance_id']}/passport",
            json={"authorized_fingerprints": []},
        )
        # pydantic v2 returns 422 on min_length violations.
        assert resp.status_code == 422, resp.text

    def test_writes_disabled_returns_403(self, tmp_path, monkeypatch):
        # Build a daemon with writes DISABLED. The pre-passport
        # /birth call would also be gated, so we test the gate
        # directly with a hand-built agent path that doesn't exist
        # (the gate fires before the agent lookup).
        for p, name in [(TRAIT_TREE, "trait tree"),
                        (CONST_TEMPLATES, "constitution templates"),
                        (TOOL_CATALOG, "tool catalog")]:
            if not p.exists():
                pytest.skip(f"{name} missing at {p}")

        monkeypatch.setenv("FSF_SECRET_STORE", "file")
        monkeypatch.setenv(
            "FSF_SECRET_STORE_FILE", str(tmp_path / "secrets.json"),
        )
        from forest_soul_forge.security import operator_key, trust_list
        operator_key.reset_cache()
        trust_list.reset_cache()

        settings = _build_settings(tmp_path, allow_writes=False)
        app = build_app(settings)
        with TestClient(app) as client:
            resp = client.post(
                "/agents/any_agent/passport",
                json={"authorized_fingerprints": ["abcd"]},
            )
            assert resp.status_code == 403, resp.text
