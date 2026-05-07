"""
Unit tests for POST /agents/{instance_id}/cycles/{cycle_id}/decision
(cycles.py L540-720).

Authored by Smith in ADR-0056 cycle 2 (plan v1) via the new E7
operator helper at dev-tools/cycle_dispatch.py. Test architecture
+ workspace fixture + parametrized bad-cycle-id test are Smith's;
operator-applied fixes were:
- Auth header swap from `Authorization: Bearer X` to the project's
  X-FSF-Token (Smith paraphrased; verbatim block hadn't included
  the auth pattern).
- Response was truncated at the 4000-token cap — completed the
  truncated TestBranchNotFound404 method directly.

Separate from test_cycles_router.py per ADR-0040 (single trust
surface per module) since that file is GET-only and already 200+
LoC. _build_decision_client redefines _build_client with
allow_write_endpoints=True so the audit chain is initialised
(cycle 1 finding — daemon/app.py L229 gates audit init on this
flag).
"""
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forest_soul_forge.daemon.app import build_app
from forest_soul_forge.daemon.config import DaemonSettings
from forest_soul_forge.registry import Registry
from conftest import seed_stub_agent  # type: ignore[import-not-found]

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------
API_TOKEN = "test-token-decision"
INSTANCE_ID = "agent_decision"

AUTH = {"X-FSF-Token": API_TOKEN}


# ---------------------------------------------------------------------------
# Git helper
# ---------------------------------------------------------------------------
def _git(repo: Path, *args: str) -> tuple[int, str, str]:
    """Run a git command in repo; return (rc, stdout, stderr)."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, result.stdout, result.stderr


def _git_must(repo: Path, *args: str) -> str:
    """Run git; raise loudly on non-zero rc. Returns stdout."""
    rc, out, err = _git(repo, *args)
    if rc != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed rc={rc} stdout={out!r} stderr={err!r}"
        )
    return out


# ---------------------------------------------------------------------------
# Workspace factory
# ---------------------------------------------------------------------------
def _build_workspace(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    _git_must(workspace, "init", "-b", "main")
    _git_must(workspace, "config", "user.email", "test@example.com")
    _git_must(workspace, "config", "user.name", "Test User")
    _git_must(workspace, "commit", "--allow-empty", "-m", "initial commit")

    # experimenter/cycle-1 — clean, has CYCLE_REPORT.md, touches feature_a.py
    _git_must(workspace, "checkout", "-b", "experimenter/cycle-1")
    (workspace / "feature_a.py").write_text(
        "def hello():\n    return 'cycle 1'\n", encoding="utf-8"
    )
    (workspace / "CYCLE_REPORT.md").write_text(
        "# Cycle 1 Report\n\ntest_outcome: passed\n", encoding="utf-8"
    )
    _git_must(workspace, "add", ".")
    _git_must(workspace, "commit", "-m", "cycle 1: add feature_a")

    # experimenter/cycle-3 — will conflict with a later main commit on feature_c.py
    _git_must(workspace, "checkout", "main")
    _git_must(workspace, "checkout", "-b", "experimenter/cycle-3")
    (workspace / "feature_c.py").write_text(
        "def conflict():\n    return 'from cycle-3'\n", encoding="utf-8"
    )
    _git_must(workspace, "add", ".")
    _git_must(workspace, "commit", "-m", "cycle 3: feature_c (will conflict)")

    # experimenter/cycle-4 — for deny-with-delete tests
    _git_must(workspace, "checkout", "main")
    _git_must(workspace, "checkout", "-b", "experimenter/cycle-4")
    (workspace / "feature_d.py").write_text(
        "def delta():\n    return 4\n", encoding="utf-8"
    )
    _git_must(workspace, "add", ".")
    _git_must(workspace, "commit", "-m", "cycle 4: feature_d")

    # experimenter/cycle-5 — for counter + deny-preserve tests
    _git_must(workspace, "checkout", "main")
    _git_must(workspace, "checkout", "-b", "experimenter/cycle-5")
    (workspace / "feature_e.py").write_text(
        "def echo():\n    return 5\n", encoding="utf-8"
    )
    _git_must(workspace, "add", ".")
    _git_must(workspace, "commit", "-m", "cycle 5: feature_e")

    # Back to main, then add a conflicting commit on feature_c.py
    _git_must(workspace, "checkout", "main")
    (workspace / "feature_c.py").write_text(
        "def conflict():\n    return 'from main'\n", encoding="utf-8"
    )
    _git_must(workspace, "add", ".")
    _git_must(workspace, "commit", "-m", "main: feature_c conflicts with cycle-3")

    _git_must(workspace, "checkout", "main")


# ---------------------------------------------------------------------------
# Client factory — allow_write_endpoints=True (audit chain gate)
# ---------------------------------------------------------------------------
def _build_decision_client(tmp_path: Path) -> tuple[TestClient, Path]:
    workspace = tmp_path / "workspace"
    _build_workspace(workspace)

    settings = DaemonSettings(
        registry_db_path=tmp_path / "registry.sqlite",
        artifacts_dir=tmp_path / "souls",
        audit_chain_path=tmp_path / "audit.jsonl",
        experimenter_workspace_path=workspace,
        api_token=API_TOKEN,
        allow_write_endpoints=True,
    )
    reg = Registry.bootstrap(settings.registry_db_path)
    seed_stub_agent(reg, INSTANCE_ID)
    reg.close()

    app = build_app(settings)
    return TestClient(app), workspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _decision(
    client: TestClient,
    cycle_id: str,
    action: str,
    *,
    note: str | None = None,
    delete_branch: bool = False,
    instance_id: str = INSTANCE_ID,
):
    return client.post(
        f"/agents/{instance_id}/cycles/{cycle_id}/decision",
        json={"action": action, "note": note, "delete_branch": delete_branch},
        headers=AUTH,
    )


def _audit_events(client: TestClient, event_type: str) -> list:
    """Pull matching events from the live audit chain on app.state."""
    chain = client.app.state.audit_chain
    return [
        e for e in chain.tail(50)
        if e.event_type == event_type
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestApproveCleanMerge:
    def test_returns_200_with_merge_sha(self, tmp_path):
        client, workspace = _build_decision_client(tmp_path)
        with client as c:
            resp = _decision(c, "cycle-1", "approve")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["action"] == "approve"
        assert body["merge_commit_sha"] is not None
        assert len(body["merge_commit_sha"]) > 0
        assert body["branch_deleted"] is False
        assert body["cycle_id"] == "cycle-1"
        assert body["branch"] == "experimenter/cycle-1"

    def test_audit_event_emitted(self, tmp_path):
        client, workspace = _build_decision_client(tmp_path)
        with client as c:
            _decision(c, "cycle-1", "approve")
            events = _audit_events(c, "experimenter_cycle_decision")
        assert len(events) == 1
        ed = events[0].event_data
        assert ed["action"] == "approve"
        assert ed["cycle_id"] == "cycle-1"
        assert ed["merge_commit_sha"] is not None
        assert ed["instance_id"] == INSTANCE_ID


class TestApproveMergeConflict:
    def test_returns_409_on_conflict(self, tmp_path):
        client, workspace = _build_decision_client(tmp_path)
        with client as c:
            resp = _decision(c, "cycle-3", "approve")
        assert resp.status_code == 409, resp.text
        assert "conflict" in resp.json()["detail"].lower()

    def test_no_audit_event_on_conflict(self, tmp_path):
        client, workspace = _build_decision_client(tmp_path)
        with client as c:
            _decision(c, "cycle-3", "approve")
            events = _audit_events(c, "experimenter_cycle_decision")
        # endpoint raises HTTPException before audit.append on conflict
        assert len(events) == 0

    def test_main_branch_intact_after_abort(self, tmp_path):
        client, workspace = _build_decision_client(tmp_path)
        with client as c:
            _decision(c, "cycle-3", "approve")
        # git status on main should be clean (merge was aborted)
        rc, out, _ = _git(workspace, "status", "--porcelain")
        assert rc == 0
        assert out.strip() == ""


class TestDenyDeleteBranchTrue:
    def test_returns_200_branch_deleted_flag(self, tmp_path):
        client, workspace = _build_decision_client(tmp_path)
        with client as c:
            resp = _decision(c, "cycle-4", "deny", delete_branch=True)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["action"] == "deny"
        assert body["branch_deleted"] is True
        assert body["merge_commit_sha"] is None

    def test_branch_absent_from_repo(self, tmp_path):
        client, workspace = _build_decision_client(tmp_path)
        with client as c:
            _decision(c, "cycle-4", "deny", delete_branch=True)
        rc, out, _ = _git(workspace, "branch", "--list", "experimenter/cycle-4")
        assert out.strip() == "", "branch should be deleted from workspace"

    def test_audit_event_records_deletion(self, tmp_path):
        client, workspace = _build_decision_client(tmp_path)
        with client as c:
            _decision(c, "cycle-4", "deny", delete_branch=True)
            events = _audit_events(c, "experimenter_cycle_decision")
        assert len(events) == 1
        assert events[0].event_data["branch_deleted"] is True
        assert events[0].event_data["action"] == "deny"


class TestDenyDeleteBranchFalse:
    def test_returns_200_branch_not_deleted(self, tmp_path):
        client, workspace = _build_decision_client(tmp_path)
        with client as c:
            resp = _decision(c, "cycle-5", "deny", delete_branch=False)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["branch_deleted"] is False

    def test_branch_still_present(self, tmp_path):
        client, workspace = _build_decision_client(tmp_path)
        with client as c:
            _decision(c, "cycle-5", "deny", delete_branch=False)
        rc, out, _ = _git(workspace, "branch", "--list", "experimenter/cycle-5")
        assert "cycle-5" in out, "branch should be preserved in workspace"


class TestCounter:
    def test_returns_200_no_merge_no_delete(self, tmp_path):
        client, workspace = _build_decision_client(tmp_path)
        with client as c:
            resp = _decision(
                c, "cycle-5", "counter",
                note="Try a tighter learning rate schedule.",
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["action"] == "counter"
        assert body["merge_commit_sha"] is None
        assert body["branch_deleted"] is False

    def test_audit_event_records_note(self, tmp_path):
        client, workspace = _build_decision_client(tmp_path)
        note_text = "Try a tighter learning rate schedule."
        with client as c:
            _decision(c, "cycle-5", "counter", note=note_text)
            events = _audit_events(c, "experimenter_cycle_decision")
        assert len(events) == 1
        assert events[0].event_data["note"] == note_text
        assert events[0].event_data["action"] == "counter"


class TestInvalidCycleId400:
    @pytest.mark.parametrize("bad_id", [
        "foo",
        "cycle-abc",
        "CYCLE-1",
        "cycle_1",
        "cycle-",
        " cycle-1",
    ])
    def test_rejects_bad_cycle_id(self, tmp_path, bad_id):
        # These reach the handler with a syntactically-valid path but
        # fail the regex at cycles.py L582 -> 400.
        client, _ = _build_decision_client(tmp_path)
        with client as c:
            resp = _decision(c, bad_id, "approve")
        assert resp.status_code == 400, (
            f"expected 400 for {bad_id!r}, got {resp.status_code}"
        )
        assert "invalid cycle_id" in resp.json()["detail"].lower()


class TestPathTraversalDefense:
    def test_dotdot_traversal_blocked_at_url_layer(self, tmp_path):
        # `../etc/passwd` in the path parameter doesn't reach the
        # cycle_decision handler at all — Starlette's URL normalization
        # collapses ".." segments before FastAPI dispatches, producing
        # a non-matching route -> 404. The L582 regex check is
        # defense-in-depth for the case where a path-traversal payload
        # somehow survives URL normalization (e.g. URL-encoded ".."
        # variants depending on ASGI server normalization rules).
        # Either way the operator's workspace is safe; this test
        # documents the layered defense.
        client, _ = _build_decision_client(tmp_path)
        with client as c:
            resp = _decision(c, "../etc/passwd", "approve")
        assert resp.status_code == 404, resp.text


class TestUnknownAgent404:
    def test_unknown_instance_id(self, tmp_path):
        client, _ = _build_decision_client(tmp_path)
        with client as c:
            resp = c.post(
                "/agents/nonexistent_agent_xyz/cycles/cycle-1/decision",
                json={"action": "approve"},
                headers=AUTH,
            )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


class TestBranchNotFound404:
    def test_valid_cycle_id_no_branch(self, tmp_path):
        # cycle_id matches the regex but no such branch exists in workspace
        # (the fixture creates cycle-1, cycle-3, cycle-4, cycle-5 — not cycle-99).
        client, _ = _build_decision_client(tmp_path)
        with client as c:
            resp = _decision(c, "cycle-99", "approve")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()
