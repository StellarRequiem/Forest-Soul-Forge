"""ADR-0056 E4 (Burst 190) — /agents/{id}/cycles router tests.

Smoke coverage for the display-mode cycles read surface:

  - GET /agents/{id}/cycles
      * 2 branches (cycle-1 with CYCLE_REPORT.md, cycle-2 without)
        return 2 summaries with differing status fields
      * branches sorted by cycle number ascending
      * workspace_available + workspace_path populated

  - GET /agents/{id}/cycles/{cycle_id}
      * Returns full diff text + cycle report content for cycle-1
      * Reports are surfaced verbatim

The router shells out to ``git`` so the fixture builds a real
temp git repo with two cycle branches off ``main``. No GitPython
dep — matches the runtime path.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
pydantic_settings = pytest.importorskip("pydantic_settings")

from fastapi.testclient import TestClient

from forest_soul_forge.daemon.app import build_app
from forest_soul_forge.daemon.config import DaemonSettings
from forest_soul_forge.registry import Registry

from conftest import seed_stub_agent  # type: ignore[import-not-found]


API_TOKEN = "test-token-cycles"
INSTANCE_ID = "agent_cycles"


# ---------------------------------------------------------------------------
# Git fixture builder
# ---------------------------------------------------------------------------
def _git(repo: Path, *args: str) -> None:
    """Run a git command in ``repo``; raise on non-zero rc so fixture
    failures surface loudly rather than producing mystery test errors.
    """
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: rc={result.returncode} "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )


def _build_workspace(workspace: Path) -> None:
    """Build a temp git repo with main + experimenter/cycle-1
    (with CYCLE_REPORT.md) + experimenter/cycle-2 (no report).
    """
    workspace.mkdir(parents=True, exist_ok=True)
    _git(workspace, "init", "-b", "main")
    _git(workspace, "config", "user.email", "test@example.com")
    _git(workspace, "config", "user.name", "Test User")
    _git(workspace, "commit", "--allow-empty", "-m", "initial commit")

    # cycle-1: cycle with a CYCLE_REPORT.md
    _git(workspace, "checkout", "-b", "experimenter/cycle-1")
    (workspace / "feature_a.py").write_text(
        "def hello():\n    return 'cycle 1'\n", encoding="utf-8",
    )
    (workspace / "CYCLE_REPORT.md").write_text(
        "# Cycle 1 Report\n\n"
        "test_outcome: passed\n\n"
        "Implements feature_a.\n",
        encoding="utf-8",
    )
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-m", "cycle 1: add feature_a")

    # cycle-2: cycle without a report (still mid-work)
    _git(workspace, "checkout", "main")
    _git(workspace, "checkout", "-b", "experimenter/cycle-2")
    (workspace / "feature_b.py").write_text(
        "def world():\n    return 'cycle 2'\n", encoding="utf-8",
    )
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-m", "cycle 2: add feature_b (wip)")

    _git(workspace, "checkout", "main")


# ---------------------------------------------------------------------------
# TestClient builder
# ---------------------------------------------------------------------------
def _build_client(tmp_path: Path) -> tuple[TestClient, Path]:
    """Stand up the daemon with an experimenter workspace, seed the
    agent FK row, and return (client, workspace_path).
    """
    workspace = tmp_path / "workspace"
    _build_workspace(workspace)

    settings = DaemonSettings(
        registry_db_path=tmp_path / "registry.sqlite",
        artifacts_dir=tmp_path / "souls",
        audit_chain_path=tmp_path / "audit.jsonl",
        experimenter_workspace_path=workspace,
        api_token=API_TOKEN,
        allow_write_endpoints=False,
    )
    # Seed the agent BEFORE build_app so the row exists at request
    # time. Registry.bootstrap is idempotent — calling it again
    # inside lifespan reuses the same DB file.
    reg = Registry.bootstrap(settings.registry_db_path)
    seed_stub_agent(reg, INSTANCE_ID)
    reg.close()

    app = build_app(settings)
    return TestClient(app), workspace


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestCyclesList:
    def test_lists_two_cycles_sorted_ascending(self, tmp_path):
        client, workspace = _build_client(tmp_path)
        with client as c:
            r = c.get(
                f"/agents/{INSTANCE_ID}/cycles",
                headers={"X-FSF-Token": API_TOKEN},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["workspace_available"] is True
        assert body["workspace_path"] == str(workspace)
        cycles = body["cycles"]
        assert len(cycles) == 2
        # Sorted by cycle number ascending.
        assert [c["cycle_id"] for c in cycles] == ["cycle-1", "cycle-2"]
        assert [c["branch"] for c in cycles] == [
            "experimenter/cycle-1",
            "experimenter/cycle-2",
        ]

    def test_status_differs_by_report_presence(self, tmp_path):
        client, _ = _build_client(tmp_path)
        with client as c:
            r = c.get(
                f"/agents/{INSTANCE_ID}/cycles",
                headers={"X-FSF-Token": API_TOKEN},
            )
        cycles = {row["cycle_id"]: row for row in r.json()["cycles"]}
        # cycle-1 has the report with "test_outcome: passed" → "passed"
        # (heuristic in _derive_status). cycle-2 has no report →
        # "pending". Either way the two MUST differ.
        c1, c2 = cycles["cycle-1"], cycles["cycle-2"]
        assert c1["has_cycle_report"] is True
        assert c2["has_cycle_report"] is False
        assert c1["status"] != c2["status"]
        assert c1["status"] in ("ready", "passed")
        assert c2["status"] == "pending"

    def test_unknown_agent_returns_404(self, tmp_path):
        client, _ = _build_client(tmp_path)
        with client as c:
            r = c.get(
                "/agents/no-such-agent/cycles",
                headers={"X-FSF-Token": API_TOKEN},
            )
        assert r.status_code == 404


class TestCycleDetail:
    def test_returns_diff_and_report_for_cycle_one(self, tmp_path):
        client, _ = _build_client(tmp_path)
        with client as c:
            r = c.get(
                f"/agents/{INSTANCE_ID}/cycles/cycle-1",
                headers={"X-FSF-Token": API_TOKEN},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["cycle_id"] == "cycle-1"
        assert body["branch"] == "experimenter/cycle-1"
        # Diff contains the file we added on cycle-1.
        assert "feature_a.py" in body["diff"]
        assert "cycle 1" in body["diff"]
        # Report content surfaced verbatim.
        assert body["cycle_report_path"] == "CYCLE_REPORT.md"
        assert body["cycle_report_content"] is not None
        assert "Cycle 1 Report" in body["cycle_report_content"]
        assert "test_outcome: passed" in body["cycle_report_content"]
        # diff_truncated is the small-payload happy path.
        assert body["diff_truncated"] is False

    def test_invalid_cycle_id_rejected(self, tmp_path):
        client, _ = _build_client(tmp_path)
        with client as c:
            r = c.get(
                f"/agents/{INSTANCE_ID}/cycles/not-a-cycle",
                headers={"X-FSF-Token": API_TOKEN},
            )
        # The router's regex guard rejects non-cycle-N ids with 400.
        assert r.status_code == 400

    def test_missing_cycle_id_returns_404(self, tmp_path):
        client, _ = _build_client(tmp_path)
        with client as c:
            r = c.get(
                f"/agents/{INSTANCE_ID}/cycles/cycle-99",
                headers={"X-FSF-Token": API_TOKEN},
            )
        assert r.status_code == 404
