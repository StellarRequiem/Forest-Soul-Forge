"""Unit tests for B432 git_local_scan.v1 builtin tool.

ADR-0084 Rule 6 substrate. Verifies the four posture dimensions:
secret detection, signed-commit posture, sync state, gitignore
coverage. Uses a real temp git repo (subprocess-driven) so the
tool's actual git invocations are exercised.

Phase A (2026-04-30) conftest convention applies: any test that
touches the agents table seeds via seed_stub_agent. These tests
don't touch agents — they only need a temp git repo.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.git_local_scan import GitLocalScanTool


def _make_repo(tmp_path: Path) -> Path:
    """Initialize a minimal git repo in tmp_path and return its root."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "commit.gpgsign", "false"],
        check=True,
    )
    return repo


def _commit(repo: Path, filename: str, content: str, msg: str) -> None:
    """Create a file + commit it."""
    (repo / filename).write_text(content)
    subprocess.run(["git", "-C", str(repo), "add", filename], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", msg, "--no-gpg-sign"],
        check=True,
    )


def _ctx_for(repo: Path) -> ToolContext:
    """Build a minimal ToolContext that allows the repo path."""
    return ToolContext(
        instance_id="test_agent",
        agent_dna="aaaaaaaaaaaa",
        role="software_engineer",
        genre="actuator",
        session_id="t",
        constraints={"allowed_paths": [str(repo)]},
    )


def test_validate_rejects_bad_repo_path_type():
    tool = GitLocalScanTool()
    with pytest.raises(ToolValidationError):
        tool.validate({"repo_path": 42})


def test_validate_rejects_bad_max_commits():
    tool = GitLocalScanTool()
    with pytest.raises(ToolValidationError):
        tool.validate({"max_commits": 0})
    with pytest.raises(ToolValidationError):
        tool.validate({"max_commits": 9999})


def test_execute_on_clean_repo_passes(tmp_path):
    """A fresh repo with .gitignore + a benign commit + no upstream
    should produce no CRITICAL findings, no committed secrets, and
    no missing gitignore patterns. ADR-0084 Tier 1 escalated unsigned
    commits to HIGH; the test rig doesn't sign, so one HIGH finding
    from the signing check is expected and explicitly carved out.
    """
    repo = _make_repo(tmp_path)
    # Healthy .gitignore covering the operator-secret patterns
    (repo / ".gitignore").write_text(
        ".env\n.env.*\n*.pem\n*.key\nid_rsa\nid_ed25519\n"
        ".streamlit/secrets.toml\ncredentials*\n"
    )
    _commit(repo, "README.md", "# clean repo\n", "initial commit")

    tool = GitLocalScanTool()
    result = asyncio.run(tool.execute({}, _ctx_for(repo)))
    out = result.output
    # No critical findings on a clean repo.
    assert out["summary"]["critical_findings"] == 0
    # Secrets scan found nothing.
    assert out["secrets"]["findings"] == []
    # Gitignore: all expected patterns present.
    assert out["gitignore"]["missing_patterns"] == []
    # Signing check flags the unsigned commit as the only HIGH.
    # Anything beyond that on a "clean" repo is a regression.
    assert out["signing"]["unsigned_count"] >= 1
    assert out["summary"]["high_findings"] == out["signing"]["unsigned_count"]


def test_execute_detects_committed_github_pat(tmp_path):
    """A file containing a fake-looking GitHub PAT triggers the
    secrets finding (CRITICAL severity)."""
    repo = _make_repo(tmp_path)
    (repo / ".gitignore").write_text(".env\n")
    # Synthetic GitHub PAT — never a real one
    leaked = "ghp_" + "0123456789abcdef0123" + "ABCDEFGHIJ"
    _commit(repo, "config.txt", f"token={leaked}\n", "leaked token")

    tool = GitLocalScanTool()
    result = asyncio.run(tool.execute({}, _ctx_for(repo)))
    out = result.output
    assert out["summary"]["critical_findings"] >= 1, (
        "Expected at least one CRITICAL finding for the committed "
        "GitHub PAT. Got: " + str(out["secrets"])
    )
    finding = out["secrets"]["findings"][0]
    assert "github" in finding["rule_id"].lower() or "token" in finding["rule_id"].lower()
    assert finding["path"] == "config.txt"
    # PAT value MUST be redacted in output
    assert finding["match_redacted"] == "<REDACTED>"


def test_execute_detects_unsigned_commits(tmp_path):
    """Commits without GPG signatures show up as 'unsigned' status N
    and produce a HIGH-severity finding."""
    repo = _make_repo(tmp_path)
    (repo / ".gitignore").write_text(
        ".env\n.env.*\n*.pem\n*.key\nid_rsa\nid_ed25519\n"
        ".streamlit/secrets.toml\ncredentials*\n"
    )
    for i in range(3):
        _commit(repo, f"f{i}.txt", f"line {i}\n", f"commit {i}")

    tool = GitLocalScanTool()
    result = asyncio.run(tool.execute({"max_commits": 10}, _ctx_for(repo)))
    out = result.output
    signing = out["signing"]
    assert signing["commits_checked"] == 3
    assert signing["unsigned_count"] == 3
    assert signing["ok"] is False
    # The unified findings should include the unsigned summary as HIGH
    assert out["summary"]["high_findings"] >= 1


def test_execute_no_upstream_reports_drift(tmp_path):
    """A repo with no upstream branch reports it in sync.note."""
    repo = _make_repo(tmp_path)
    _commit(repo, "f.txt", "x\n", "x")
    tool = GitLocalScanTool()
    result = asyncio.run(tool.execute({}, _ctx_for(repo)))
    sync = result.output["sync"]
    assert sync["upstream_ref"] is None
    assert sync["ok"] is False
    assert "upstream" in sync["note"]


def test_execute_refuses_path_outside_allowed_paths(tmp_path):
    """A repo_path outside the agent's allowed_paths must refuse."""
    repo = _make_repo(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "init", "-q", str(other)], check=True)

    ctx = ToolContext(
        instance_id="t",
        agent_dna="aaaaaaaaaaaa",
        role="x",
        genre="x",
        session_id="t",
        constraints={"allowed_paths": [str(repo)]},  # only repo allowed
    )
    tool = GitLocalScanTool()
    with pytest.raises(ToolValidationError):
        asyncio.run(tool.execute({"repo_path": str(other)}, ctx))


def test_execute_refuses_non_git_dir(tmp_path):
    """A path that exists but isn't a git repo must refuse cleanly."""
    not_a_repo = tmp_path / "not_a_repo"
    not_a_repo.mkdir()
    ctx = ToolContext(
        instance_id="t",
        agent_dna="aaaaaaaaaaaa",
        role="x",
        genre="x",
        session_id="t",
        constraints={"allowed_paths": [str(not_a_repo)]},
    )
    tool = GitLocalScanTool()
    with pytest.raises(ToolValidationError):
        asyncio.run(tool.execute({}, ctx))
