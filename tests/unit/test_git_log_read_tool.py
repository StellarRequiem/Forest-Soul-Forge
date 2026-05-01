"""Tests for git_log_read.v1 (Phase G.1.A third programming primitive).

Coverage:
- TestValidate            — input shape: path, max_count, ref, since/until/
                             author, paths_filter, timeout_seconds
- TestLocateGit           — _locate_git helper (PATH lookup)
- TestParseLogOutput      — delimited git output -> structured commits
- TestValidateRefString   — argument-injection defense
- TestPathAllowlist       — allowed_paths gate; mirrors ruff_lint semantics
- TestExecute             — happy path, empty repo, no commits matching filter,
                             missing path, outside-allowed, not-a-git-repo,
                             ref injection, paths_filter outside allowed,
                             timeout, git-not-installed, truncation,
                             real-repo round-trip
- TestRegistration        — tool registers via register_builtins; catalog entry
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.git_log_read import (
    DEFAULT_MAX_COUNT,
    DEFAULT_TIMEOUT_SECONDS,
    GIT_LOG_MAX_COUNT_HARD_CAP,
    GitLogReadError,
    GitLogReadTool,
    GitNotInstalledError,
    NotAGitRepoError,
    _is_within_any,
    _locate_git,
    _parse_log_output,
    _resolve_allowlist,
    _validate_ref_string,
    _FS,
    _RS,
)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Real-repo fixture — creates a tiny git repo in tmp_path with a couple of
# commits so we can run git_log_read against an actual repository in tests.
# ---------------------------------------------------------------------------
def _init_test_repo(tmp_path: Path) -> Path:
    """Initialize a git repo with 3 commits and return the repo path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        "GIT_AUTHOR_NAME": "Test Author",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test Author",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        # Avoid picking up the operator's ~/.gitconfig
        "HOME": str(tmp_path),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "PATH": __import__("os").environ.get("PATH", ""),
    }

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            env=env, check=True, capture_output=True, text=True,
        )

    git("init", "-b", "main")
    (repo / "a.txt").write_text("hello\n")
    git("add", "a.txt")
    git("commit", "-m", "first commit\n\nbody line one\nbody line two")
    (repo / "b.txt").write_text("world\n")
    git("add", "b.txt")
    git("commit", "-m", "second commit")
    (repo / "a.txt").write_text("HELLO\n")
    git("add", "a.txt")
    git("commit", "-m", "third commit\n\nfinal body")
    return repo


@pytest.fixture
def repo(tmp_path):
    """Provide (ctx, repo_path) for a real 3-commit git repo. Skipped if
    git is not on PATH (e.g., minimal CI environments)."""
    if _locate_git() is None:
        pytest.skip("git not on PATH; real-repo tests skipped")
    repo_path = _init_test_repo(tmp_path)
    ctx = ToolContext(
        instance_id="i1", agent_dna="d" * 12,
        role="code_architect", genre="observer",
        session_id="s1",
        constraints={"allowed_paths": [str(tmp_path)]},
    )
    return ctx, repo_path


# ===========================================================================
# Validation
# ===========================================================================
class TestValidate:
    def test_missing_path_rejected(self):
        with pytest.raises(ToolValidationError, match="path"):
            GitLogReadTool().validate({})

    def test_empty_path_rejected(self):
        with pytest.raises(ToolValidationError, match="path"):
            GitLogReadTool().validate({"path": "  "})

    def test_non_string_path_rejected(self):
        with pytest.raises(ToolValidationError, match="path"):
            GitLogReadTool().validate({"path": 42})

    def test_invalid_max_count_rejected(self):
        with pytest.raises(ToolValidationError, match="max_count"):
            GitLogReadTool().validate({"path": "/tmp", "max_count": 0})
        with pytest.raises(ToolValidationError, match="max_count"):
            GitLogReadTool().validate(
                {"path": "/tmp", "max_count": GIT_LOG_MAX_COUNT_HARD_CAP + 1},
            )

    def test_invalid_timeout_rejected(self):
        with pytest.raises(ToolValidationError, match="timeout_seconds"):
            GitLogReadTool().validate({"path": "/tmp", "timeout_seconds": 0})
        with pytest.raises(ToolValidationError, match="timeout_seconds"):
            GitLogReadTool().validate(
                {"path": "/tmp", "timeout_seconds": 121},
            )

    def test_invalid_ref_rejected(self):
        with pytest.raises(ToolValidationError, match="ref"):
            GitLogReadTool().validate({"path": "/tmp", "ref": ""})
        with pytest.raises(ToolValidationError, match="ref"):
            GitLogReadTool().validate({"path": "/tmp", "ref": "-rf"})

    def test_invalid_paths_filter_rejected(self):
        with pytest.raises(ToolValidationError, match="paths_filter"):
            GitLogReadTool().validate({"path": "/tmp", "paths_filter": "not-a-list"})
        with pytest.raises(ToolValidationError, match="paths_filter"):
            GitLogReadTool().validate({"path": "/tmp", "paths_filter": [""]})
        with pytest.raises(ToolValidationError, match="paths_filter"):
            GitLogReadTool().validate({"path": "/tmp", "paths_filter": [42]})

    def test_invalid_string_opts_rejected(self):
        for opt in ("since", "until", "author"):
            with pytest.raises(ToolValidationError, match=opt):
                GitLogReadTool().validate({"path": "/tmp", opt: ""})
            with pytest.raises(ToolValidationError, match=opt):
                GitLogReadTool().validate({"path": "/tmp", opt: 42})

    def test_valid_minimal_args(self):
        GitLogReadTool().validate({"path": "/tmp"})

    def test_valid_full_args(self):
        GitLogReadTool().validate({
            "path": "/tmp/repo",
            "max_count": 10,
            "ref": "main",
            "since": "2026-01-01",
            "until": "2026-12-31",
            "author": "alex@example.com",
            "paths_filter": ["src/", "tests/"],
            "timeout_seconds": 60,
        })


# ===========================================================================
# _locate_git
# ===========================================================================
class TestLocateGit:
    def test_returns_path_when_git_on_path(self):
        with mock.patch(
            "forest_soul_forge.tools.builtin.git_log_read.shutil.which",
            return_value="/usr/bin/git",
        ):
            assert _locate_git() == "/usr/bin/git"

    def test_returns_none_when_git_missing(self):
        with mock.patch(
            "forest_soul_forge.tools.builtin.git_log_read.shutil.which",
            return_value=None,
        ):
            assert _locate_git() is None


# ===========================================================================
# _validate_ref_string (argument-injection defense)
# ===========================================================================
class TestValidateRefString:
    def test_normal_refs_accepted(self):
        for ref in ("main", "feature/foo", "v1.2.3", "abc1234", "HEAD"):
            _validate_ref_string(ref)  # no raise

    def test_dash_prefix_rejected(self):
        with pytest.raises(ToolValidationError, match="must not start with"):
            _validate_ref_string("-rf")
        with pytest.raises(ToolValidationError, match="must not start with"):
            _validate_ref_string("--all")


# ===========================================================================
# _parse_log_output
# ===========================================================================
class TestParseLogOutput:
    def test_empty_input_returns_empty_list(self):
        assert _parse_log_output("") == []
        assert _parse_log_output("   \n  ") == []

    def test_single_commit_parsed(self):
        record = (
            f"abc123def{_FS}Alex{_FS}alex@x.com{_FS}2026-04-30T10:00:00-04:00"
            f"{_FS}2026-04-30T10:00:00-04:00{_FS}parent1 parent2"
            f"{_FS}first commit{_FS}body line\nmore body{_RS}"
        )
        commits = _parse_log_output(record)
        assert len(commits) == 1
        c = commits[0]
        assert c["sha"] == "abc123def"
        assert c["author_name"] == "Alex"
        assert c["author_email"] == "alex@x.com"
        assert c["author_date"] == "2026-04-30T10:00:00-04:00"
        assert c["commit_date"] == "2026-04-30T10:00:00-04:00"
        assert c["parents"] == ["parent1", "parent2"]
        assert c["subject"] == "first commit"
        assert c["body"] == "body line\nmore body"

    def test_root_commit_no_parents(self):
        record = (
            f"sha1{_FS}A{_FS}a@x{_FS}2026-04-30T10:00:00Z"
            f"{_FS}2026-04-30T10:00:00Z{_FS}{_FS}root commit{_FS}{_RS}"
        )
        commits = _parse_log_output(record)
        assert commits[0]["parents"] == []

    def test_multiple_commits(self):
        # Two records back to back.
        rec = (
            f"sha1{_FS}A{_FS}a@x{_FS}d1{_FS}d1{_FS}p1{_FS}s1{_FS}{_RS}"
            f"sha2{_FS}B{_FS}b@x{_FS}d2{_FS}d2{_FS}{_FS}s2{_FS}{_RS}"
        )
        commits = _parse_log_output(rec)
        assert [c["sha"] for c in commits] == ["sha1", "sha2"]
        assert commits[1]["parents"] == []

    def test_short_record_padded(self):
        # Defensive: if git ever emits a short record, we pad rather than crash.
        record = f"sha1{_FS}A{_FS}a@x{_RS}"
        commits = _parse_log_output(record)
        assert commits[0]["sha"] == "sha1"
        assert commits[0]["body"] == ""
        assert commits[0]["parents"] == []


# ===========================================================================
# Path allowlist
# ===========================================================================
class TestPathAllowlist:
    def test_resolve_allowlist_skips_empty_entries(self):
        roots = _resolve_allowlist(["", "  ", "/tmp"])
        assert len(roots) == 1
        assert str(roots[0]) == str(Path("/tmp").resolve())

    def test_is_within_root_match(self, tmp_path):
        roots = _resolve_allowlist([str(tmp_path)])
        assert _is_within_any(tmp_path.resolve(), roots) is True

    def test_is_within_descendant(self, tmp_path):
        sub = tmp_path / "a" / "b"
        sub.mkdir(parents=True)
        roots = _resolve_allowlist([str(tmp_path)])
        assert _is_within_any(sub.resolve(), roots) is True

    def test_outside_blocked(self, tmp_path):
        roots = _resolve_allowlist([str(tmp_path)])
        outside = (tmp_path.parent / "elsewhere").resolve()
        assert _is_within_any(outside, roots) is False


# ===========================================================================
# execute()
# ===========================================================================
class TestExecute:
    def test_real_repo_returns_commits(self, repo):
        ctx, repo_path = repo
        tool = GitLogReadTool()
        result = _run(tool.execute({"path": str(repo_path)}, ctx))
        assert result.output["commits_count"] == 3
        assert result.output["truncated"] is False
        assert result.output["ref"] == "HEAD"
        # Most-recent commit first
        assert result.output["commits"][0]["subject"] == "third commit"
        assert result.output["commits"][2]["subject"] == "first commit"
        # First commit has body
        assert "body line one" in result.output["commits"][2]["body"]
        # Second commit has empty body
        assert result.output["commits"][1]["body"] == ""

    def test_max_count_truncation(self, repo):
        ctx, repo_path = repo
        tool = GitLogReadTool()
        result = _run(tool.execute(
            {"path": str(repo_path), "max_count": 2}, ctx,
        ))
        assert result.output["commits_count"] == 2
        assert result.output["truncated"] is True

    def test_paths_filter_narrows_results(self, repo):
        ctx, repo_path = repo
        tool = GitLogReadTool()
        # b.txt was only created in commit 2; only 1 commit should match.
        result = _run(tool.execute(
            {"path": str(repo_path), "paths_filter": ["b.txt"]}, ctx,
        ))
        assert result.output["commits_count"] == 1
        assert result.output["commits"][0]["subject"] == "second commit"

    def test_ref_specific(self, repo):
        ctx, repo_path = repo
        tool = GitLogReadTool()
        # HEAD~2 should give just the first commit.
        result = _run(tool.execute(
            {"path": str(repo_path), "ref": "HEAD~2"}, ctx,
        ))
        assert result.output["commits_count"] == 1
        assert result.output["commits"][0]["subject"] == "first commit"

    def test_missing_allowed_paths_refuses(self, tmp_path):
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12,
            role="r", genre="g", session_id="s",
            constraints={},
        )
        with pytest.raises(GitLogReadError, match="allowed_paths"):
            _run(GitLogReadTool().execute({"path": str(tmp_path)}, ctx))

    def test_outside_allowed_blocked(self, tmp_path):
        # Create allowed root that doesn't contain target.
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        target = tmp_path / "elsewhere"
        target.mkdir()
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12,
            role="r", genre="g", session_id="s",
            constraints={"allowed_paths": [str(allowed)]},
        )
        with pytest.raises(GitLogReadError, match="outside"):
            _run(GitLogReadTool().execute({"path": str(target)}, ctx))

    def test_nonexistent_path_refuses(self, tmp_path):
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12,
            role="r", genre="g", session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        with pytest.raises(GitLogReadError, match="does not exist"):
            _run(GitLogReadTool().execute(
                {"path": str(tmp_path / "nope")}, ctx,
            ))

    def test_path_must_be_directory(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hi")
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12,
            role="r", genre="g", session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        with pytest.raises(GitLogReadError, match="must be a directory"):
            _run(GitLogReadTool().execute({"path": str(f)}, ctx))

    def test_not_a_git_repo_refuses(self, tmp_path):
        if _locate_git() is None:
            pytest.skip("git not on PATH")
        # tmp_path is allowed and exists, but no git init has been run.
        # Need a clean subdir so we don't get bitten by an ancestor repo.
        clean = tmp_path / "no_git"
        clean.mkdir()
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12,
            role="r", genre="g", session_id="s",
            constraints={"allowed_paths": [str(clean)]},
        )
        # If the OS hosting this test happens to have a parent .git,
        # git -C will walk up to it. We sidestep that by running in
        # a hermetic subdir AND setting GIT_CEILING_DIRECTORIES via
        # the subprocess env. Easier: mock _locate_git to a wrapper
        # that won't find .git. Actually simplest: use mock.patch on
        # subprocess.run to simulate the not-a-repo error.
        fake = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="",
            stderr="fatal: not a git repository (or any of the parent directories): .git\n",
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.git_log_read.subprocess.run",
            return_value=fake,
        ), mock.patch(
            "forest_soul_forge.tools.builtin.git_log_read._locate_git",
            return_value="/usr/bin/git",
        ):
            with pytest.raises(NotAGitRepoError):
                _run(GitLogReadTool().execute({"path": str(clean)}, ctx))

    def test_paths_filter_outside_allowed_refuses(self, repo):
        ctx, repo_path = repo
        # An absolute path outside the allowed root.
        with pytest.raises(GitLogReadError, match="paths_filter"):
            _run(GitLogReadTool().execute(
                {"path": str(repo_path), "paths_filter": ["/etc/passwd"]},
                ctx,
            ))

    def test_timeout_refuses(self, tmp_path):
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12,
            role="r", genre="g", session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.git_log_read._locate_git",
            return_value="/usr/bin/git",
        ), mock.patch(
            "forest_soul_forge.tools.builtin.git_log_read.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=30),
        ):
            with pytest.raises(GitLogReadError, match="timed out"):
                _run(GitLogReadTool().execute(
                    {"path": str(tmp_path), "timeout_seconds": 30}, ctx,
                ))

    def test_git_not_installed_refuses(self, tmp_path):
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12,
            role="r", genre="g", session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.git_log_read._locate_git",
            return_value=None,
        ):
            with pytest.raises(GitNotInstalledError):
                _run(GitLogReadTool().execute({"path": str(tmp_path)}, ctx))

    def test_metadata_records_invocation(self, repo):
        ctx, repo_path = repo
        result = _run(GitLogReadTool().execute({"path": str(repo_path)}, ctx))
        assert "git_bin" in result.metadata
        assert result.metadata["max_count"] == DEFAULT_MAX_COUNT
        assert result.metadata["actual_count"] >= 3


# ===========================================================================
# Registration
# ===========================================================================
class TestRegistration:
    def test_tool_registered(self):
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.builtin import register_builtins

        registry = ToolRegistry()
        register_builtins(registry)
        tool = registry.get("git_log_read", "1")
        assert tool is not None
        assert tool.side_effects == "read_only"

    def test_catalog_entry_present(self):
        import yaml
        from pathlib import Path

        catalog_path = (
            Path(__file__).parent.parent.parent
            / "config" / "tool_catalog.yaml"
        )
        with open(catalog_path) as f:
            catalog = yaml.safe_load(f)

        tools = catalog["tools"]
        assert "git_log_read.v1" in tools
        entry = tools["git_log_read.v1"]
        assert entry["side_effects"] == "read_only"
        # No required_initiative_level — read_only doesn't need it.
        assert "required_initiative_level" not in entry

    def test_no_initiative_level_attribute(self):
        # Read-only tools deliberately don't carry required_initiative_level.
        tool = GitLogReadTool()
        assert not hasattr(tool, "required_initiative_level") or \
            getattr(tool, "required_initiative_level", None) is None
