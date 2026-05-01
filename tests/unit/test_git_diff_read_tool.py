"""Tests for git_diff_read.v1 (Phase G.1.A fourth programming primitive).

Coverage:
- TestValidate            — input shape: path, mode, refs, paths_filter,
                             max_files, max_lines_per_file, timeout
- TestLocateGit           — _locate_git PATH lookup
- TestValidateRefString   — argument-injection defense
- TestParseNumstat        — numstat tab-separated output parsing
- TestParseDiffOutput     — unified-patch parsing into structured files
- TestPathAllowlist       — allowed_paths gate
- TestExecute             — three modes against a real repo, refusal cases
- TestRegistration        — tool + catalog entry
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.git_diff_read import (
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_LINES_PER_FILE,
    GIT_DIFF_MAX_FILES_HARD_CAP,
    GIT_DIFF_MAX_LINES_HARD_CAP,
    VALID_MODES,
    GitDiffReadError,
    GitDiffReadTool,
    GitNotInstalledError,
    NotAGitRepoError,
    _is_within_any,
    _locate_git,
    _parse_diff_output,
    _parse_numstat,
    _resolve_allowlist,
    _validate_ref_string,
)


def _run(coro):
    return asyncio.run(coro)


def _init_test_repo(tmp_path: Path) -> Path:
    """Init a tiny git repo with three commits + a working-tree change."""
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        "GIT_AUTHOR_NAME": "Test Author",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test Author",
        "GIT_COMMITTER_EMAIL": "test@example.com",
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
    (repo / "a.txt").write_text("line1\nline2\nline3\n")
    git("add", "a.txt")
    git("commit", "-m", "first")
    (repo / "a.txt").write_text("line1\nLINE2\nline3\nline4\n")
    git("add", "a.txt")
    git("commit", "-m", "second — modify a + grow")
    (repo / "b.txt").write_text("brand new file\n")
    git("add", "b.txt")
    git("commit", "-m", "third — add b")
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
    return ctx, repo_path


# ===========================================================================
# Validation
# ===========================================================================
class TestValidate:
    def test_missing_path_rejected(self):
        with pytest.raises(ToolValidationError, match="path"):
            GitDiffReadTool().validate({})

    def test_invalid_mode_rejected(self):
        with pytest.raises(ToolValidationError, match="mode"):
            GitDiffReadTool().validate({"path": "/tmp", "mode": "bogus"})

    def test_refs_mode_requires_both_refs(self):
        with pytest.raises(ToolValidationError, match="ref_a"):
            GitDiffReadTool().validate({"path": "/tmp", "mode": "refs"})
        with pytest.raises(ToolValidationError, match="ref_b"):
            GitDiffReadTool().validate(
                {"path": "/tmp", "mode": "refs", "ref_a": "main"},
            )

    def test_refs_must_not_start_with_dash(self):
        with pytest.raises(ToolValidationError, match="must not start with"):
            GitDiffReadTool().validate({
                "path": "/tmp", "mode": "refs",
                "ref_a": "-rf", "ref_b": "main",
            })

    def test_non_refs_mode_rejects_refs(self):
        with pytest.raises(ToolValidationError, match="must not be set"):
            GitDiffReadTool().validate({
                "path": "/tmp", "mode": "working", "ref_a": "main",
            })

    def test_max_files_bounds(self):
        with pytest.raises(ToolValidationError, match="max_files"):
            GitDiffReadTool().validate({"path": "/tmp", "max_files": 0})
        with pytest.raises(ToolValidationError, match="max_files"):
            GitDiffReadTool().validate(
                {"path": "/tmp", "max_files": GIT_DIFF_MAX_FILES_HARD_CAP + 1},
            )

    def test_max_lines_bounds(self):
        with pytest.raises(ToolValidationError, match="max_lines_per_file"):
            GitDiffReadTool().validate(
                {"path": "/tmp", "max_lines_per_file": 0},
            )
        with pytest.raises(ToolValidationError, match="max_lines_per_file"):
            GitDiffReadTool().validate({
                "path": "/tmp",
                "max_lines_per_file": GIT_DIFF_MAX_LINES_HARD_CAP + 1,
            })

    def test_timeout_bounds(self):
        with pytest.raises(ToolValidationError, match="timeout_seconds"):
            GitDiffReadTool().validate({"path": "/tmp", "timeout_seconds": 0})
        with pytest.raises(ToolValidationError, match="timeout_seconds"):
            GitDiffReadTool().validate({"path": "/tmp", "timeout_seconds": 121})

    def test_invalid_paths_filter(self):
        with pytest.raises(ToolValidationError, match="paths_filter"):
            GitDiffReadTool().validate(
                {"path": "/tmp", "paths_filter": "not-a-list"},
            )
        with pytest.raises(ToolValidationError, match="paths_filter"):
            GitDiffReadTool().validate({"path": "/tmp", "paths_filter": [""]})

    def test_valid_minimal(self):
        GitDiffReadTool().validate({"path": "/tmp"})  # mode defaults to working

    def test_valid_refs_full(self):
        GitDiffReadTool().validate({
            "path": "/tmp/repo",
            "mode": "refs",
            "ref_a": "main",
            "ref_b": "feature/foo",
            "paths_filter": ["src/", "tests/"],
            "max_files": 50,
            "max_lines_per_file": 200,
            "timeout_seconds": 60,
        })

    def test_valid_modes_constant(self):
        assert set(VALID_MODES) == {"refs", "staged", "working"}


# ===========================================================================
# _validate_ref_string
# ===========================================================================
class TestValidateRefString:
    def test_normal_refs_accepted(self):
        for r in ("main", "feature/foo", "v1.2.3", "abc1234"):
            _validate_ref_string(r)

    def test_dash_prefix_rejected(self):
        with pytest.raises(ToolValidationError):
            _validate_ref_string("-rf")


# ===========================================================================
# _parse_numstat
# ===========================================================================
class TestParseNumstat:
    def test_text_files(self):
        text = "5\t2\tsrc/a.py\n10\t0\tnew.txt\n0\t8\tdeleted.txt\n"
        out = _parse_numstat(text)
        assert out["src/a.py"] == (5, 2)
        assert out["new.txt"] == (10, 0)
        assert out["deleted.txt"] == (0, 8)

    def test_binary_uses_minus_one(self):
        text = "-\t-\timg.png\n"
        out = _parse_numstat(text)
        assert out["img.png"] == (-1, -1)

    def test_rename_uses_post_arrow_path(self):
        # ``old.py => new.py`` form
        text = "5\t3\told.py => new.py\n"
        out = _parse_numstat(text)
        assert "new.py" in out
        # ``{a => b}/file`` form (we strip braces best-effort)
        text2 = "1\t1\t{old => new}/file.py\n"
        out2 = _parse_numstat(text2)
        # The post-arrow piece is " new}/file.py"; we strip braces.
        assert any(k.endswith("/file.py") for k in out2.keys())

    def test_empty_input(self):
        assert _parse_numstat("") == {}

    def test_malformed_lines_skipped(self):
        text = "no-tabs-here\n5\t2\tgood.py\n"
        out = _parse_numstat(text)
        assert "good.py" in out
        assert len(out) == 1


# ===========================================================================
# _parse_diff_output
# ===========================================================================
class TestParseDiffOutput:
    def test_modified_file(self):
        diff = (
            "diff --git a/a.txt b/a.txt\n"
            "index 1234567..abcdef0 100644\n"
            "--- a/a.txt\n"
            "+++ b/a.txt\n"
            "@@ -1,3 +1,4 @@\n"
            " line1\n"
            "-line2\n"
            "+LINE2\n"
            " line3\n"
            "+line4\n"
        )
        files = _parse_diff_output(diff, max_lines_per_file=100, numstat_map={})
        assert len(files) == 1
        f = files[0]
        assert f["status"] == "modified"
        assert f["old_path"] == "a.txt"
        assert f["new_path"] == "a.txt"
        assert len(f["hunks"]) == 1
        h = f["hunks"][0]
        assert h["old_start"] == 1
        assert h["old_count"] == 3
        assert h["new_start"] == 1
        assert h["new_count"] == 4
        assert "-line2" in h["body"]
        assert "+LINE2" in h["body"]

    def test_added_file(self):
        diff = (
            "diff --git a/new.txt b/new.txt\n"
            "new file mode 100644\n"
            "index 0000000..abcdef0\n"
            "--- /dev/null\n"
            "+++ b/new.txt\n"
            "@@ -0,0 +1,2 @@\n"
            "+hello\n"
            "+world\n"
        )
        files = _parse_diff_output(diff, max_lines_per_file=100, numstat_map={})
        assert files[0]["status"] == "added"
        assert files[0]["old_path"] == ""

    def test_deleted_file(self):
        diff = (
            "diff --git a/old.txt b/old.txt\n"
            "deleted file mode 100644\n"
            "index abcdef0..0000000\n"
            "--- a/old.txt\n"
            "+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n"
            "-bye\n"
            "-world\n"
        )
        files = _parse_diff_output(diff, max_lines_per_file=100, numstat_map={})
        assert files[0]["status"] == "deleted"
        assert files[0]["new_path"] == ""

    def test_binary_file(self):
        diff = (
            "diff --git a/img.png b/img.png\n"
            "index 1234567..abcdef0 100644\n"
            "Binary files a/img.png and b/img.png differ\n"
        )
        files = _parse_diff_output(diff, max_lines_per_file=100, numstat_map={})
        assert files[0]["is_binary"] is True
        assert files[0]["hunks"] == []

    def test_truncation_per_file(self):
        diff = (
            "diff --git a/big.txt b/big.txt\n"
            "--- a/big.txt\n"
            "+++ b/big.txt\n"
            "@@ -1,5 +1,5 @@\n"
            + "".join(f"+line{i}\n" for i in range(10))
        )
        files = _parse_diff_output(diff, max_lines_per_file=3, numstat_map={})
        assert files[0]["body_truncated"] is True
        # Body should hold exactly 3 lines.
        assert files[0]["hunks"][0]["body"].count("\n") == 2

    def test_numstat_overlay(self):
        diff = (
            "diff --git a/a.txt b/a.txt\n"
            "--- a/a.txt\n"
            "+++ b/a.txt\n"
            "@@ -1,1 +1,1 @@\n"
            "-x\n"
            "+y\n"
        )
        files = _parse_diff_output(
            diff, max_lines_per_file=100,
            numstat_map={"a.txt": (1, 1)},
        )
        assert files[0]["additions"] == 1
        assert files[0]["deletions"] == 1

    def test_numstat_marks_binary(self):
        diff = (
            "diff --git a/img.png b/img.png\n"
            "Binary files a/img.png and b/img.png differ\n"
        )
        files = _parse_diff_output(
            diff, max_lines_per_file=100,
            numstat_map={"img.png": (-1, -1)},
        )
        assert files[0]["is_binary"] is True
        assert files[0]["additions"] == -1

    def test_empty_input(self):
        assert _parse_diff_output("", 100, {}) == []


# ===========================================================================
# Path allowlist
# ===========================================================================
class TestPathAllowlist:
    def test_resolve_skips_empty(self):
        roots = _resolve_allowlist(["", "  ", "/tmp"])
        assert len(roots) == 1

    def test_within_root(self, tmp_path):
        roots = _resolve_allowlist([str(tmp_path)])
        assert _is_within_any(tmp_path.resolve(), roots) is True

    def test_outside_blocked(self, tmp_path):
        roots = _resolve_allowlist([str(tmp_path)])
        outside = (tmp_path.parent / "elsewhere").resolve()
        assert _is_within_any(outside, roots) is False


# ===========================================================================
# execute()
# ===========================================================================
class TestExecute:
    def test_refs_mode_diffs_two_commits(self, repo):
        ctx, repo_path = repo
        result = _run(GitDiffReadTool().execute({
            "path": str(repo_path),
            "mode": "refs",
            "ref_a": "HEAD~2",
            "ref_b": "HEAD~1",
        }, ctx))
        # Second commit modified a.txt
        assert result.output["mode"] == "refs"
        assert result.output["files_count"] == 1
        f = result.output["files"][0]
        assert f["new_path"] == "a.txt"
        assert f["status"] == "modified"
        # numstat overlay should give us real counts
        assert f["additions"] >= 1
        assert f["deletions"] >= 1

    def test_refs_mode_full_history(self, repo):
        ctx, repo_path = repo
        # Diff initial commit against HEAD — should show changes to a.txt
        # and the addition of b.txt.
        result = _run(GitDiffReadTool().execute({
            "path": str(repo_path),
            "mode": "refs",
            "ref_a": "HEAD~2",
            "ref_b": "HEAD",
        }, ctx))
        assert result.output["files_count"] == 2
        statuses = sorted(f["status"] for f in result.output["files"])
        assert "added" in statuses
        assert "modified" in statuses

    def test_working_mode_no_changes(self, repo):
        ctx, repo_path = repo
        # Clean working tree → empty diff
        result = _run(GitDiffReadTool().execute({
            "path": str(repo_path),
            "mode": "working",
        }, ctx))
        assert result.output["files_count"] == 0
        assert result.output["truncated"] is False

    def test_working_mode_dirty_tree(self, repo):
        ctx, repo_path = repo
        # Add an unstaged change
        (repo_path / "a.txt").write_text("totally different\n")
        result = _run(GitDiffReadTool().execute({
            "path": str(repo_path),
            "mode": "working",
        }, ctx))
        assert result.output["files_count"] == 1
        assert result.output["files"][0]["new_path"] == "a.txt"

    def test_staged_mode(self, repo):
        ctx, repo_path = repo
        # Stage a change but don't commit
        (repo_path / "a.txt").write_text("staged change\n")
        subprocess.run(
            ["git", "-C", str(repo_path), "add", "a.txt"],
            check=True, capture_output=True,
        )
        result = _run(GitDiffReadTool().execute({
            "path": str(repo_path),
            "mode": "staged",
        }, ctx))
        assert result.output["mode"] == "staged"
        assert result.output["files_count"] == 1

    def test_paths_filter_narrows(self, repo):
        ctx, repo_path = repo
        result = _run(GitDiffReadTool().execute({
            "path": str(repo_path),
            "mode": "refs",
            "ref_a": "HEAD~2",
            "ref_b": "HEAD",
            "paths_filter": ["a.txt"],
        }, ctx))
        assert result.output["files_count"] == 1
        assert result.output["files"][0]["new_path"] == "a.txt"

    def test_missing_allowed_paths_refuses(self, tmp_path):
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s", constraints={},
        )
        with pytest.raises(GitDiffReadError, match="allowed_paths"):
            _run(GitDiffReadTool().execute({"path": str(tmp_path)}, ctx))

    def test_outside_allowed_blocked(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        target = tmp_path / "elsewhere"
        target.mkdir()
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(allowed)]},
        )
        with pytest.raises(GitDiffReadError, match="outside"):
            _run(GitDiffReadTool().execute({"path": str(target)}, ctx))

    def test_nonexistent_path_refuses(self, tmp_path):
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        with pytest.raises(GitDiffReadError, match="does not exist"):
            _run(GitDiffReadTool().execute(
                {"path": str(tmp_path / "nope")}, ctx,
            ))

    def test_path_must_be_directory(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hi")
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        with pytest.raises(GitDiffReadError, match="must be a directory"):
            _run(GitDiffReadTool().execute({"path": str(f)}, ctx))

    def test_not_a_git_repo_refuses(self, tmp_path):
        clean = tmp_path / "no_git"
        clean.mkdir()
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(clean)]},
        )
        fake = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="",
            stderr="fatal: not a git repository (or any of the parent directories): .git\n",
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.git_diff_read.subprocess.run",
            return_value=fake,
        ), mock.patch(
            "forest_soul_forge.tools.builtin.git_diff_read._locate_git",
            return_value="/usr/bin/git",
        ):
            with pytest.raises(NotAGitRepoError):
                _run(GitDiffReadTool().execute({"path": str(clean)}, ctx))

    def test_paths_filter_outside_allowed_refuses(self, repo):
        ctx, repo_path = repo
        with pytest.raises(GitDiffReadError, match="paths_filter"):
            _run(GitDiffReadTool().execute({
                "path": str(repo_path),
                "paths_filter": ["/etc/passwd"],
            }, ctx))

    def test_timeout_refuses(self, tmp_path):
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.git_diff_read._locate_git",
            return_value="/usr/bin/git",
        ), mock.patch(
            "forest_soul_forge.tools.builtin.git_diff_read.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=30),
        ):
            with pytest.raises(GitDiffReadError, match="timed out"):
                _run(GitDiffReadTool().execute({"path": str(tmp_path)}, ctx))

    def test_git_not_installed_refuses(self, tmp_path):
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.git_diff_read._locate_git",
            return_value=None,
        ):
            with pytest.raises(GitNotInstalledError):
                _run(GitDiffReadTool().execute({"path": str(tmp_path)}, ctx))

    def test_metadata_records_invocation(self, repo):
        ctx, repo_path = repo
        result = _run(GitDiffReadTool().execute({
            "path": str(repo_path), "mode": "working",
        }, ctx))
        assert result.metadata["max_files"] == DEFAULT_MAX_FILES
        assert result.metadata["max_lines_per_file"] == DEFAULT_MAX_LINES_PER_FILE
        assert "git_bin" in result.metadata


# ===========================================================================
# Registration
# ===========================================================================
class TestRegistration:
    def test_tool_registered(self):
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.builtin import register_builtins

        registry = ToolRegistry()
        register_builtins(registry)
        tool = registry.get("git_diff_read", "1")
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

        entry = catalog["tools"]["git_diff_read.v1"]
        assert entry["side_effects"] == "read_only"
        assert "required_initiative_level" not in entry
