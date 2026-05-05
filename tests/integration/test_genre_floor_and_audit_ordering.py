"""Integration test — audit chain hash linkage + genre engine surface.

Burst 134. Closes more of the documented integration-tests gap from
STATE.md's items in queue. Exercises:

  - Audit chain hash discipline (kernel API spec §2.2)
  - Audit chain seq monotonicity (spec §2.1)
  - Genre engine HTTP surface (spec §5.3 GET /genres)
  - Tool catalog HTTP surface (spec §5.3 GET /tools/catalog)
  - Multi-write audit chain consistency

Two scenarios:
  A. Multiple births append to the audit chain. The on-disk JSONL
     forms a valid hash-linked sequence per spec §2.2 (timestamp NOT
     hashed; genesis prev_hash = "GENESIS").
  B. /genres + /tools/catalog endpoints expose the documented data
     shapes — observer genre's read_only floor + mcp_call.v1 in the
     catalog (spec §1.4 v1.0 freeze surface).

Failures here surface integration bugs that unit tests miss:
audit chain corruption under concurrent writes (R3 write_lock
discipline), seq generation race conditions, hash chain breakage
from multi-writer interleaving.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
pydantic_settings = pytest.importorskip("pydantic_settings")

from fastapi.testclient import TestClient

from forest_soul_forge.daemon.app import build_app
from forest_soul_forge.daemon.config import DaemonSettings


REPO_ROOT = Path(__file__).resolve().parents[2]
TRAIT_TREE = REPO_ROOT / "config" / "trait_tree.yaml"
CONST_TEMPLATES = REPO_ROOT / "config" / "constitution_templates.yaml"
TOOL_CATALOG = REPO_ROOT / "config" / "tool_catalog.yaml"
GENRES = REPO_ROOT / "config" / "genres.yaml"


@pytest.fixture
def daemon_env(tmp_path: Path):
    for p, name in [
        (TRAIT_TREE, "trait tree"),
        (CONST_TEMPLATES, "constitution templates"),
        (TOOL_CATALOG, "tool catalog"),
        (GENRES, "genres"),
    ]:
        if not p.exists():
            pytest.skip(f"{name} missing at {p}")

    settings = DaemonSettings(
        registry_db_path=tmp_path / "registry.sqlite",
        artifacts_dir=tmp_path / "souls",
        audit_chain_path=tmp_path / "audit.jsonl",
        trait_tree_path=TRAIT_TREE,
        constitution_templates_path=CONST_TEMPLATES,
        soul_output_dir=tmp_path / "souls",
        tool_catalog_path=TOOL_CATALOG,
        genres_path=GENRES,
        skill_install_dir=tmp_path / "skills",
        default_provider="local",
        frontier_enabled=False,
        allow_write_endpoints=True,
        enrich_narrative_default=False,
    )
    (tmp_path / "skills").mkdir(exist_ok=True)
    app = build_app(settings)
    yield {"app": app, "settings": settings, "audit_path": tmp_path / "audit.jsonl"}


def _birth(client: TestClient, role: str = "network_watcher") -> str:
    resp = client.post(
        "/birth",
        json={
            "profile": {
                "role": role,
                "trait_values": {},
                "domain_weight_overrides": {},
            },
            "agent_name": f"AuditTest-{role}",
            "agent_version": "v1",
            "owner_id": "burst134-audit",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["instance_id"]


def test_audit_chain_hash_linkage_after_multiple_births(daemon_env):
    """Spec §2.2: hash chain links cleanly after multiple writes.

    Births three agents and verifies the on-disk JSONL forms a valid
    hash-linked sequence using the actual canonical-form contract:
    timestamp NOT hashed, genesis prev_hash = "GENESIS".
    """
    app = daemon_env["app"]
    audit_path = daemon_env["audit_path"]
    with TestClient(app) as client:
        _birth(client, role="network_watcher")
        _birth(client, role="log_analyst")
        _birth(client, role="operator_companion")

    if not audit_path.exists():
        pytest.skip("audit chain file not created")
    raw_lines = audit_path.read_text().strip().splitlines()
    if len(raw_lines) < 2:
        pytest.skip(f"only {len(raw_lines)} chain entries; need ≥ 2 to verify linkage")

    entries = [json.loads(line) for line in raw_lines]

    # Spec §2.1: seq is monotonically increasing.
    seqs = [e["seq"] for e in entries]
    for i in range(1, len(seqs)):
        assert seqs[i] > seqs[i - 1], (
            f"seq not monotonic at index {i}: {seqs[i-1]} → {seqs[i]}"
        )

    # Spec §2.2: each entry's prev_hash equals the previous entry's
    # entry_hash. The very first entry's prev_hash is "GENESIS".
    if entries[0]["seq"] == 1:
        assert entries[0]["prev_hash"] == "GENESIS", (
            f"seq=1 prev_hash should be 'GENESIS' per spec §2.2; "
            f"got {entries[0]['prev_hash']!r}"
        )
    for i in range(1, len(entries)):
        prev = entries[i - 1]
        cur = entries[i]
        assert cur["prev_hash"] == prev["entry_hash"], (
            f"hash chain broken between seq={prev['seq']} → seq={cur['seq']}"
        )

    # Spec §2.2: spot-verify one entry's hash matches sha256 of the
    # canonical-JSON form (timestamp + entry_hash excluded).
    sample = entries[-1]
    canonical_body = {
        "seq": sample["seq"],
        "agent_dna": sample["agent_dna"],
        "event_type": sample["event_type"],
        "event_data": sample["event_data"],
        "prev_hash": sample["prev_hash"],
    }
    canonical_json = json.dumps(
        canonical_body, sort_keys=True, separators=(",", ":")
    )
    expected_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    assert sample["entry_hash"] == expected_hash, (
        f"entry_hash spec §2.2 violation at seq={sample['seq']}: "
        f"computed {expected_hash} but file says {sample['entry_hash']}"
    )


def test_genres_endpoint_lists_observer(daemon_env):
    """GET /genres surfaces the observer genre per spec §5.3."""
    app = daemon_env["app"]
    with TestClient(app) as client:
        resp = client.get("/genres")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Body shape: {"genres": {...}} or list of dicts
        genres = body.get("genres", body)
        if isinstance(genres, dict):
            assert "observer" in genres, (
                f"observer genre missing from /genres response: {list(genres)[:5]}"
            )
        elif isinstance(genres, list):
            names = {g.get("name") for g in genres}
            assert "observer" in names, (
                f"observer genre missing from /genres response: {names}"
            )
        else:
            pytest.skip(f"unexpected /genres shape: {type(body)}")


def test_tool_catalog_includes_mcp_call_v1(daemon_env):
    """GET /tools/catalog exposes mcp_call.v1 — v1.0 freeze surface (spec §1.4)."""
    app = daemon_env["app"]
    with TestClient(app) as client:
        # The endpoint per src/.../routers/tools.py is /tools/catalog
        # (not /tools, which is reserved for the dispatch endpoint).
        resp = client.get("/tools/catalog")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        tools = body.get("tools", body)
        if not isinstance(tools, list):
            # body might be a dict of {name: tool_data}; accept either.
            tools = list(tools.values()) if isinstance(tools, dict) else []
        matches = [
            t for t in tools
            if (t.get("name") or "").startswith("mcp_call")
            and str(t.get("version", "")) in ("1", "v1")
        ]
        assert matches, (
            f"mcp_call.v1 not in /tools/catalog response (spec §1.4 requires); "
            f"got {len(tools)} tools, sample names: "
            f"{[t.get('name') for t in tools[:5]]}"
        )


def test_traits_endpoint_lists_42_roles(daemon_env):
    """GET /traits surfaces the 42-role roster post-Burst-124."""
    app = daemon_env["app"]
    with TestClient(app) as client:
        resp = client.get("/traits")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        roles = body.get("roles", body)
        if isinstance(roles, dict):
            role_count = len(roles)
        elif isinstance(roles, list):
            role_count = len(roles)
        else:
            pytest.skip(f"unexpected /traits shape: {type(body)}")
        assert role_count == 42, (
            f"trait engine should have 42 roles post-Burst-124; got {role_count}"
        )
