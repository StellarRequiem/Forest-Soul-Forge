"""End-to-end tests for ``POST /agents/{instance_id}/tools/call`` — ADR-0019 T2.

The fixture births a real agent through ``/birth`` so the constitution
file the dispatcher reads is actually on disk. Then we drive the
dispatch endpoint and check status codes + audit-chain entries.
"""
from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
pydantic_settings = pytest.importorskip("pydantic_settings")

from fastapi.testclient import TestClient

from forest_soul_forge.daemon.app import build_app
from forest_soul_forge.daemon.config import DaemonSettings
from forest_soul_forge.daemon.providers import (
    ProviderHealth,
    ProviderRegistry,
    ProviderStatus,
    TaskKind,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
TRAIT_TREE = REPO_ROOT / "config" / "trait_tree.yaml"
CONST_TEMPLATES = REPO_ROOT / "config" / "constitution_templates.yaml"
TOOL_CATALOG = REPO_ROOT / "config" / "tool_catalog.yaml"
GENRES = REPO_ROOT / "config" / "genres.yaml"


class _StubProvider:
    name = "local"

    def __init__(self) -> None:
        self._models = {k: "stub:latest" for k in TaskKind}

    @property
    def models(self) -> dict:
        return dict(self._models)

    async def complete(self, prompt, *, task_kind=TaskKind.CONVERSATION, **_):
        return f"[stub] {prompt}"

    async def healthcheck(self):
        return ProviderHealth(
            name="local", status=ProviderStatus.OK, base_url="http://stub",
            models=self._models, details={"loaded": [], "missing": []},
            error=None,
        )


@pytest.fixture
def dispatch_env(tmp_path: Path):
    """Daemon wired with the real configs so /birth produces a constitution
    that lists ``timestamp_window.v1`` (network_watcher's archetype kit
    includes it). The test then dispatches that tool against the freshly
    born agent.
    """
    for p, name in [(TRAIT_TREE, "trait tree"),
                    (CONST_TEMPLATES, "constitution templates"),
                    (TOOL_CATALOG, "tool catalog")]:
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
        # genres.yaml is loaded best-effort; passing the real path
        # exercises the genre-claim path when present, and falls back
        # to an empty engine when not.
        genres_path=GENRES,
        default_provider="local",
        frontier_enabled=False,
        allow_write_endpoints=True,
        enrich_narrative_default=False,
    )
    app = build_app(settings)
    with TestClient(app) as client:
        app.state.providers = ProviderRegistry(
            providers={"local": _StubProvider(), "frontier": _StubProvider()},
            default="local",
        )
        # Birth a network_watcher — its archetype kit includes
        # timestamp_window.v1 so the constitution.yaml will list it.
        resp = client.post("/birth", json={
            "profile": {
                "role": "network_watcher",
                "trait_values": {},
                "domain_weight_overrides": {},
            },
            "agent_name": "DispatchTestWatcher",
            "agent_version": "v1",
            "owner_id": "test-owner",
        })
        assert resp.status_code == 201, resp.text
        instance_id = resp.json()["instance_id"]
        yield client, app, instance_id


class TestDispatchEndpoint:
    def test_succeeded_returns_200_with_result(self, dispatch_env):
        client, _, instance_id = dispatch_env
        resp = client.post(
            f"/agents/{instance_id}/tools/call",
            json={
                "tool_name": "timestamp_window",
                "tool_version": "1",
                "session_id": "sess-1",
                "args": {"expression": "last 5 minutes",
                         "anchor": "2026-04-26T12:00:00Z"},
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "succeeded"
        assert body["tool_key"] == "timestamp_window.v1"
        assert body["call_count_after"] == 1
        assert body["result"]["output"]["span_seconds"] == 300

    def test_unknown_agent_404(self, dispatch_env):
        client, _, _ = dispatch_env
        resp = client.post(
            "/agents/no-such-agent/tools/call",
            json={
                "tool_name": "timestamp_window",
                "tool_version": "1",
                "session_id": "sess-1",
                "args": {"expression": "last 5 minutes"},
            },
        )
        assert resp.status_code == 404

    def test_unknown_tool_404(self, dispatch_env):
        client, _, instance_id = dispatch_env
        resp = client.post(
            f"/agents/{instance_id}/tools/call",
            json={
                "tool_name": "no_such_tool",
                "tool_version": "1",
                "session_id": "sess-1",
                "args": {},
            },
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["detail"]["reason"] == "unknown_tool"

    def test_bad_args_400(self, dispatch_env):
        client, _, instance_id = dispatch_env
        resp = client.post(
            f"/agents/{instance_id}/tools/call",
            json={
                "tool_name": "timestamp_window",
                "tool_version": "1",
                "session_id": "sess-1",
                "args": {},  # missing 'expression'
            },
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["reason"] == "bad_args"

    def test_repeated_dispatch_increments_counter(self, dispatch_env):
        client, _, instance_id = dispatch_env
        for n in range(1, 4):
            resp = client.post(
                f"/agents/{instance_id}/tools/call",
                json={
                    "tool_name": "timestamp_window",
                    "tool_version": "1",
                    "session_id": "sess-1",
                    "args": {"expression": "last 1 minutes"},
                },
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["call_count_after"] == n


class TestPendingCallsEndpoints:
    """ADR-0019 T3 — list / detail / approve / reject end-to-end.

    Setup: birth a network_watcher and override its constitution to
    require approval on the timestamp_window tool. Then dispatch once
    to seed a pending row, and exercise all four endpoints.
    """

    def _force_approval_required(self, instance_id: str, app):
        """Patch the agent's on-disk constitution so the timestamp_window
        tool flips to requires_human_approval=True. The test owns the
        artifact tree so this is fine; in production the operator
        would re-birth or use tools_add semantics.

        Implementation note: the constitution YAML lists multiple tools
        each with their own ``requires_human_approval`` line. Earlier
        version of this helper just did ``str.replace(..., count=1)``,
        which always flipped the FIRST tool's flag — and that worked
        only because timestamp_window was first in the network_watcher
        kit. After the 2026-04-30 C-1 zombie-tool dissection
        (traffic_flow_local now precedes timestamp_window in the kit),
        the helper needs to scope the replacement to timestamp_window's
        specific block. Use a multi-line search anchored on the tool
        name.
        """
        registry = app.state.registry
        agent = registry.get_agent(instance_id)
        const_path = Path(agent.constitution_path)
        text = const_path.read_text(encoding="utf-8")
        # Find the timestamp_window tool's block + flip its flag in
        # isolation. The block looks roughly like:
        #
        #     - constraints:
        #         max_calls_per_session: ...
        #         requires_human_approval: false
        #       name: timestamp_window
        #
        # We search for "name: timestamp_window" and walk back to the
        # nearest preceding ``requires_human_approval: false`` line.
        marker = "name: timestamp_window"
        marker_pos = text.find(marker)
        if marker_pos < 0:
            raise RuntimeError(
                "test fixture: timestamp_window not in agent's constitution"
            )
        # Walk backward to find the nearest requires_human_approval line.
        prefix = text[:marker_pos]
        rha_pos = prefix.rfind("requires_human_approval: false")
        if rha_pos < 0:
            raise RuntimeError(
                "test fixture: timestamp_window has no requires_human_approval flag"
            )
        new_text = (
            text[:rha_pos]
            + "requires_human_approval: true"
            + text[rha_pos + len("requires_human_approval: false"):]
        )
        const_path.write_text(new_text, encoding="utf-8")

    def test_full_lifecycle_approve(self, dispatch_env):
        client, app, instance_id = dispatch_env
        self._force_approval_required(instance_id, app)

        # 1. Dispatch — gates.
        resp = client.post(
            f"/agents/{instance_id}/tools/call",
            json={
                "tool_name": "timestamp_window",
                "tool_version": "1",
                "session_id": "sess-1",
                "args": {"expression": "last 1 minutes"},
            },
        )
        assert resp.status_code == 202, resp.text
        ticket_id = resp.json()["ticket_id"]

        # 2. List shows the ticket.
        listed = client.get(f"/agents/{instance_id}/pending_calls").json()
        assert listed["count"] == 1
        assert listed["pending_calls"][0]["ticket_id"] == ticket_id
        assert listed["pending_calls"][0]["args"]["expression"] == "last 1 minutes"

        # 3. Detail endpoint.
        detail = client.get(f"/pending_calls/{ticket_id}").json()
        assert detail["status"] == "pending"
        assert detail["tool_key"] == "timestamp_window.v1"

        # 4. Approve — returns the dispatch outcome.
        approved = client.post(
            f"/pending_calls/{ticket_id}/approve",
            json={"operator_id": "alex"},
        )
        assert approved.status_code == 200, approved.text
        body = approved.json()
        assert body["status"] == "succeeded"
        assert body["call_count_after"] == 1

        # 5. Ticket no longer pending.
        again = client.get(f"/agents/{instance_id}/pending_calls").json()
        assert again["count"] == 0
        # ...but visible with status=all.
        full = client.get(
            f"/agents/{instance_id}/pending_calls?status=all"
        ).json()
        assert full["count"] == 1
        assert full["pending_calls"][0]["status"] == "approved"

    def test_full_lifecycle_reject(self, dispatch_env):
        client, app, instance_id = dispatch_env
        self._force_approval_required(instance_id, app)
        resp = client.post(
            f"/agents/{instance_id}/tools/call",
            json={
                "tool_name": "timestamp_window",
                "tool_version": "1",
                "session_id": "sess-1",
                "args": {"expression": "last 1 minutes"},
            },
        )
        ticket_id = resp.json()["ticket_id"]

        rejected = client.post(
            f"/pending_calls/{ticket_id}/reject",
            json={"operator_id": "alex", "reason": "not now"},
        )
        assert rejected.status_code == 200, rejected.text
        body = rejected.json()
        assert body["status"] == "rejected"
        assert body["decision_reason"] == "not now"

    def test_double_approve_409(self, dispatch_env):
        client, app, instance_id = dispatch_env
        self._force_approval_required(instance_id, app)
        resp = client.post(
            f"/agents/{instance_id}/tools/call",
            json={
                "tool_name": "timestamp_window",
                "tool_version": "1",
                "session_id": "sess-1",
                "args": {"expression": "last 1 minutes"},
            },
        )
        ticket_id = resp.json()["ticket_id"]
        client.post(
            f"/pending_calls/{ticket_id}/approve",
            json={"operator_id": "alex"},
        )
        # Second approve should 409.
        again = client.post(
            f"/pending_calls/{ticket_id}/approve",
            json={"operator_id": "alex"},
        )
        assert again.status_code == 409

    def test_unknown_ticket_404(self, dispatch_env):
        client, _, _ = dispatch_env
        resp = client.get("/pending_calls/no-such-ticket")
        assert resp.status_code == 404
        resp = client.post(
            "/pending_calls/no-such-ticket/approve",
            json={"operator_id": "alex"},
        )
        assert resp.status_code == 404


class TestCharacterSheetStats:
    """ADR-0019 T4 — character sheet pulls live stats from tool_calls."""

    def test_fresh_agent_has_not_yet_measured(self, dispatch_env):
        client, _, instance_id = dispatch_env
        resp = client.get(f"/agents/{instance_id}/character-sheet")
        assert resp.status_code == 200, resp.text
        stats = resp.json()["stats"]
        assert stats["not_yet_measured"] is True
        assert stats["total_invocations"] == 0
        assert stats["per_tool"] == []

    def test_dispatch_populates_stats(self, dispatch_env):
        client, _, instance_id = dispatch_env
        # Dispatch twice — both succeed, both should show up on the sheet.
        for _ in range(2):
            resp = client.post(
                f"/agents/{instance_id}/tools/call",
                json={
                    "tool_name": "timestamp_window",
                    "tool_version": "1",
                    "session_id": "sess-1",
                    "args": {"expression": "last 1 minutes"},
                },
            )
            assert resp.status_code == 200

        sheet = client.get(f"/agents/{instance_id}/character-sheet").json()
        stats = sheet["stats"]
        assert stats["not_yet_measured"] is False
        assert stats["total_invocations"] == 2
        assert stats["failed_invocations"] == 0
        # timestamp_window is pure — no tokens / cost.
        assert stats["total_tokens_used"] is None
        assert stats["total_cost_usd"] is None
        # Per-tool breakdown shows the one tool used twice.
        per_tool = stats["per_tool"]
        assert len(per_tool) == 1
        assert per_tool[0]["tool_key"] == "timestamp_window.v1"
        assert per_tool[0]["count"] == 2
