"""Tests for git_blame_read.v1 (Phase G.1.A fifth programming primitive)."""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.git_blame_read import (
    DEFAULT_MAX_LINES,
    GIT_BLAME_MAX_LINES_HARD_CAP,
    GitBlameReadError,
    GitBlameReadTool,
    GitNotInstalledError,
    NotAGitRepoError,
    _format_unix_with_tz,
    _is_within_any,
    _locate_git,
    _parse_porcelain,
    _resolve_allowlist,
    _validate_ref_string,
)


def _run(coro):
    return asyncio.run(coro)


def _init_test_repo(tmp_path: Path) -> Path:
    """Init a tiny git repo with a file that has 3 commits touching it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        "GIT_AUTHOR_NAME": "Alex",
        "GIT_AUTHOR_EMAIL": "alex@example.com",
        "GIT_COMMITTER_NAME": "Alex",
        "GIT_COMMITTER_EMAIL": "alex@example.com",
        "HOME": str(tmp_path),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "PATH": __import__("os").environ.get("PATH", ""),
    }

    def git(*args: str, custom_env=None) -> subprocess.CompletedProcess:
        e = dict(env)
        if custom_env:
            e.update(custom_env)
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            env=e, check=True, capture_output=True, text=True,
        )

    git("init", "-b", "main")
    (repo / "a.py").write_text("def add(a, b):\n    return a + b\n")
    git("add", "a.py")
    git("commit", "-m", "first: define add")
    (repo / "a.py").write_text(
        "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n"
    )
    git("add", "a.py")
    git(
        "commit", "-m", "second: add sub",
        custom_env={"GIT_AUTHOR_NAME": "Beth", "GIT_AUTHOR_EMAIL": "beth@example.com"},
    )
    (repo / "a.py").write_text(
        "def add(a, b):\n    # nothing fancy\n    return a + b\n\n"
        "def sub(a, b):\n    return a - b\n"
    )
    git("add", "a.py")
    git("commit", "-m", "third: comment on add")
    return repo


@pytest.fixture
def repo(tmp_path):
    if _locate_git() is None:
        pytest.skip("git not on PATH")
    repo_path = _init_test_repo(tmp_path)
    ctx = ToolContext(
        instance_id="i1", agent_dna="d" * 12,
        role="code_reviewer", genre="guardian",
        session_id="s1",
        constraints={"allowed_paths": [str(tmp_path)]},
    )
    return ctx, repo_path / "a.py"


# ===========================================================================
# Validation
# ===========================================================================
class TestValidate:
    def test_missing_path_rejected(self):
        with pytest.raises(ToolValidationError, match="path"):
            GitBlameReadTool().validate({})

    def test_invalid_ref_rejected(self):
        with pytest.raises(ToolValidationError, match="ref"):
            GitBlameReadTool().validate({"path": "/tmp/x", "ref": ""})
        with pytest.raises(ToolValidationError, match="must not start with"):
            GitBlameReadTool().validate({"path": "/tmp/x", "ref": "-rf"})

    def test_invalid_line_range_rejected(self):
        with pytest.raises(ToolValidationError, match="line_range"):
            GitBlameReadTool().validate(
                {"path": "/tmp/x", "line_range": [1, 2, 3]},
            )
        with pytest.raises(ToolValidationError, match="line_range"):
            GitBlameReadTool().validate(
                {"path": "/tmp/x", "line_range": "1,2"},
            )
        with pytest.raises(ToolValidationError, match="line_range"):
            GitBlameReadTool().validate(
                {"path": "/tmp/x", "line_range": [0, 5]},
            )
        with pytest.raises(ToolValidationError, match="line_range"):
            GitBlameReadTool().validate(
                {"path": "/tmp/x", "line_range": [10, 5]},
            )

    def test_invalid_max_lines_rejected(self):
        with pytest.raises(ToolValidationError, match="max_lines"):
            GitBlameReadTool().validate({"path": "/tmp/x", "max_lines": 0})
        with pytest.raises(ToolValidationError, match="max_lines"):
            GitBlameReadTool().validate(
                {"path": "/tmp/x", "max_lines": GIT_BLAME_MAX_LINES_HARD_CAP + 1},
            )

    def test_invalid_timeout_rejected(self):
        with pytest.raises(ToolValidationError, match="timeout_seconds"):
            GitBlameReadTool().validate({"path": "/tmp/x", "timeout_seconds": 0})
        with pytest.raises(ToolValidationError, match="timeout_seconds"):
            GitBlameReadTool().validate({"path": "/tmp/x", "timeout_seconds": 121})

    def test_valid_minimal(self):
        GitBlameReadTool().validate({"path": "/tmp/x"})

    def test_valid_full(self):
        GitBlameReadTool().validate({
            "path": "/tmp/repo/a.py",
            "ref": "main",
            "line_range": [1, 100],
            "max_lines": 200,
            "timeout_seconds": 60,
        })


# ===========================================================================
# _validate_ref_string
# ===========================================================================
class TestValidateRefString:
    def test_normal_refs(self):
        for r in ("main", "feature/x", "v1.0", "abc1234", "HEAD"):
            _validate_ref_string(r)

    def test_dash_rejected(self):
        with pytest.raises(ToolValidationError):
            _validate_ref_string("-rf")


# ===========================================================================
# _format_unix_with_tz
# ===========================================================================
class TestFormatUnixWithTz:
    def test_utc(self):
        # 2026-05-02T12:00:00 UTC (1777723200 unix seconds)
        s = _format_unix_with_tz(1777723200, "+0000")
        assert s.startswith("2026-05-02T12:00:00")
        assert s.endswith("+00:00")

    def test_offset(self):
        s = _format_unix_with_tz(1777723200, "-0400")
        assert s.endswith("-04:00")
        # -0400 from 12:00 UTC = 08:00 local
        assert "T08:00:00" in s

    def test_malformed_tz_falls_back(self):
        s = _format_unix_with_tz(1777723200, "garbage")
        # Falls back to ISO UTC; should still contain the date.
        assert "2026-05-02" in s


# ===========================================================================
# _parse_porcelain
# ===========================================================================
class TestParsePorcelain:
    def test_single_blame_group(self):
        out = (
            "abc1234567890abcdef1234567890abcdef12345 1 1 2\n"
            "author Alex\n"
            "author-mail <alex@x.com>\n"
            "author-time 1777723200\n"
            "author-tz +0000\n"
            "committer Alex\n"
            "committer-mail <alex@x.com>\n"
            "committer-time 1777723200\n"
            "committer-tz +0000\n"
            "summary first commit\n"
            "filename a.py\n"
            "\tdef add(a, b):\n"
            "abc1234567890abcdef1234567890abcdef12345 2 2\n"
            "\t    return a + b\n"
        )
        lines = _parse_porcelain(out)
        assert len(lines) == 2
        assert lines[0]["line_no"] == 1
        assert lines[0]["original_line_no"] == 1
        assert lines[0]["author_name"] == "Alex"
        assert lines[0]["author_email"] == "alex@x.com"
        assert lines[0]["summary"] == "first commit"
        assert lines[0]["content"] == "def add(a, b):"
        assert lines[1]["line_no"] == 2
        assert lines[1]["content"] == "    return a + b"
        # Same sha → same metadata reused
        assert lines[1]["author_name"] == "Alex"

    def test_multiple_commits(self):
        out = (
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 1 1\n"
            "author Alex\n"
            "author-mail <alex@x.com>\n"
            "author-time 1\n"
            "author-tz +0000\n"
            "summary one\n"
            "filename a.py\n"
            "\tline-from-alex\n"
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb 1 2 1\n"
            "author Beth\n"
            "author-mail <beth@x.com>\n"
            "author-time 2\n"
            "author-tz +0000\n"
            "summary two\n"
            "filename a.py\n"
            "\tline-from-beth\n"
        )
        lines = _parse_porcelain(out)
        assert lines[0]["author_name"] == "Alex"
        assert lines[1]["author_name"] == "Beth"

    def test_empty_output(self):
        assert _parse_porcelain("") == []


# ===========================================================================
# Path allowlist
# ===========================================================================
class TestPathAllowlist:
    def test_within(self, tmp_path):
        roots = _resolve_allowlist([str(tmp_path)])
        assert _is_within_any(tmp_path.resolve(), roots) is True

    def test_outside(self, tmp_path):
        roots = _resolve_allowlist([str(tmp_path)])
        outside = (tmp_path.parent / "elsewhere").resolve()
        assert _is_within_any(outside, roots) is False


# ===========================================================================
# execute()
# ===========================================================================
class TestExecute:
    def test_real_blame_returns_lines(self, repo):
        ctx, file_path = repo
        result = _run(GitBlameReadTool().execute({"path": str(file_path)}, ctx))
        # File has 6 lines after third commit
        assert result.output["lines_count"] == 6
        assert result.output["truncated"] is False
        # First line should be the original Alex commit
        first = result.output["lines"][0]
        assert first["author_name"] == "Alex"
        assert "def add" in first["content"]
        # The sub function lines should be authored by Beth
        sub_line = next(
            l for l in result.output["lines"]
            if "def sub" in l["content"]
        )
        assert sub_line["author_name"] == "Beth"

    def test_line_range_narrows(self, repo):
        ctx, file_path = repo
        result = _run(GitBlameReadTool().execute(
            {"path": str(file_path), "line_range": [1, 2]}, ctx,
        ))
        assert result.output["lines_count"] == 2

    def test_truncation(self, repo):
        ctx, file_path = repo
        result = _run(GitBlameReadTool().execute(
            {"path": str(file_path), "max_lines": 2}, ctx,
        ))
        assert result.output["lines_count"] == 2
        assert result.output["truncated"] is True

    def test_at_specific_ref(self, repo):
        ctx, file_path = repo
        # HEAD~2 has only the first commit's content (2 lines)
        result = _run(GitBlameReadTool().execute(
            {"path": str(file_path), "ref": "HEAD~2"}, ctx,
        ))
        assert result.output["ref"] == "HEAD~2"
        assert result.output["lines_count"] == 2

    def test_missing_allowed_paths_refuses(self, tmp_path):
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s", constraints={},
        )
        f = tmp_path / "x.py"
        f.write_text("hi")
        with pytest.raises(GitBlameReadError, match="allowed_paths"):
            _run(GitBlameReadTool().execute({"path": str(f)}, ctx))

    def test_outside_allowed_blocked(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        outside = tmp_path / "outside.py"
        outside.write_text("hi")
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(allowed)]},
        )
        with pytest.raises(GitBlameReadError, match="outside"):
            _run(GitBlameReadTool().execute({"path": str(outside)}, ctx))

    def test_path_must_be_file(self, tmp_path):
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        with pytest.raises(GitBlameReadError, match="regular file"):
            _run(GitBlameReadTool().execute({"path": str(tmp_path)}, ctx))

    def test_nonexistent_path_refuses(self, tmp_path):
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        with pytest.raises(GitBlameReadError, match="does not exist"):
            _run(GitBlameReadTool().execute(
                {"path": str(tmp_path / "nope.py")}, ctx,
            ))

    def test_not_a_git_repo_refuses(self, tmp_path):
        clean = tmp_path / "no_git"
        clean.mkdir()
        f = clean / "x.py"
        f.write_text("hi")
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(clean)]},
        )
        fake = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="",
            stderr="fatal: not a git repository\n",
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.git_blame_read.subprocess.run",
            return_value=fake,
        ), mock.patch(
            "forest_soul_forge.tools.builtin.git_blame_read._locate_git",
            return_value="/usr/bin/git",
        ):
            with pytest.raises(NotAGitRepoError):
                _run(GitBlameReadTool().execute({"path": str(f)}, ctx))

    def test_timeout_refuses(self, tmp_path):
        f = tmp_path / "x.py"
        f.write_text("hi")
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.git_blame_read._locate_git",
            return_value="/usr/bin/git",
        ), mock.patch(
            "forest_soul_forge.tools.builtin.git_blame_read.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=30),
        ):
            with pytest.raises(GitBlameReadError, match="timed out"):
                _run(GitBlameReadTool().execute({"path": str(f)}, ctx))

    def test_git_not_installed_refuses(self, tmp_path):
        f = tmp_path / "x.py"
        f.write_text("hi")
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.git_blame_read._locate_git",
            return_value=None,
        ):
            with pytest.raises(GitNotInstalledError):
                _run(GitBlameReadTool().execute({"path": str(f)}, ctx))

    def test_metadata_records_invocation(self, repo):
        ctx, file_path = repo
        result = _run(GitBlameReadTool().execute({"path": str(file_path)}, ctx))
        assert "git_bin" in result.metadata
        assert result.metadata["max_lines"] == DEFAULT_MAX_LINES


# ===========================================================================
# Registration
# ===========================================================================
class TestRegistration:
    def test_tool_registered(self):
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.builtin import register_builtins

        registry = ToolRegistry()
        register_builtins(registry)
        tool = registry.get("git_blame_read", "1")
        assert tool is not None
        assert tool.side_effects == "read_only"

    def test_catalog_entry_present(self):
        import yaml
        catalog_path = (
            Path(__file__).parent.parent.parent
            / "config" / "tool_catalog.yaml"
        )
        with open(catalog_path) as f:
            catalog = yaml.safe_load(f)
        entry = catalog["tools"]["git_blame_read.v1"]
        assert entry["side_effects"] == "read_only"
        assert "required_initiative_level" not in entry
