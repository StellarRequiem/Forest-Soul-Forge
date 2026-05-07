"""``/agents/{instance_id}/cycles`` — ADR-0056 E4 (Burst 190).

Display-mode read surface for Smith's branch-isolated cycles.
Reads the experimenter workspace via git subprocess (no
GitPython dep) and surfaces cycle metadata to the chat tab.

Two endpoints:

  GET /agents/{instance_id}/cycles
    List view. One row per branch matching experimenter/cycle-*.
    Cheap: one rev-parse + one diff-stat per branch. Suitable
    for a 5-second refresh tick on the chat pane.

  GET /agents/{instance_id}/cycles/{cycle_id}
    Detail view. Full diff (size-capped) + full commit message
    + cycle report content if present + parsed requested_tools.
    More expensive — fired on row expand only.

Both endpoints are READ-ONLY. The decision actions (approve /
deny / counter) ship as part of E5 because they overlap with
the self-augmentation flow's merge + tools_add automation.

Per ADR-0056 D5: cycle_id is the branch name without the
'experimenter/' prefix. Stable across daemon restarts because
git branch names don't change without explicit rename.

Per ADR-0001 D2: read-only. Touches no agent identity.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from forest_soul_forge.daemon.deps import (
    get_registry,
    require_api_token,
)
from forest_soul_forge.daemon.schemas import (
    CycleDetail,
    CycleListOut,
    CycleSummary,
)
from forest_soul_forge.registry import Registry
from forest_soul_forge.registry.registry import UnknownAgentError


router = APIRouter(prefix="/agents", tags=["cycles"])


# ---------------------------------------------------------------------------
# Constants — defaults can be lifted to DaemonSettings if operators care.
# ---------------------------------------------------------------------------
BRANCH_PATTERN = re.compile(r"^experimenter/cycle-(\d+)$")
DEFAULT_MAX_DIFF_BYTES = 200 * 1024  # 200 KB — keeps response payload bounded
DEFAULT_BASE_BRANCH = "main"
CYCLE_REPORT_CANDIDATES = (
    "CYCLE_REPORT.md",
    "docs/cycles/CYCLE_REPORT.md",
    # Per-cycle filename pattern. The router substitutes the
    # cycle_id at lookup time.
    "docs/cycles/{cycle_id}.md",
)
TIMEOUT_S = 8.0  # any single git subprocess that takes >8s is bug-shaped


# ---------------------------------------------------------------------------
# git subprocess helpers
# ---------------------------------------------------------------------------
def _run_git(
    repo: Path, *args: str,
) -> tuple[int, str, str]:
    """Run ``git -C <repo> <args>`` and return (returncode, stdout, stderr).
    Never raises — caller decides what to do with non-zero rc.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"git timed out after {TIMEOUT_S}s"
    except FileNotFoundError:
        return 127, "", "git binary not found on PATH"
    except Exception as e:  # noqa: BLE001 — defensive
        return 1, "", f"{type(e).__name__}: {e}"


def _list_cycle_branches(repo: Path) -> list[tuple[str, str]]:
    """Return list of (cycle_id, branch_name) for every local branch
    matching the experimenter pattern. Sorted by cycle number ascending.
    """
    rc, out, _ = _run_git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/")
    if rc != 0:
        return []
    pairs: list[tuple[int, str, str]] = []
    for line in out.splitlines():
        line = line.strip()
        m = BRANCH_PATTERN.match(line)
        if not m:
            continue
        n = int(m.group(1))
        cycle_id = line.split("/", 1)[1]  # cycle-N
        pairs.append((n, cycle_id, line))
    pairs.sort(key=lambda t: t[0])
    return [(c, b) for (_, c, b) in pairs]


def _head_info(
    repo: Path, branch: str,
) -> tuple[str, str, str, str] | None:
    """Return (short_sha, first_line, full_message, iso_timestamp) for
    the branch's HEAD commit, or None on error.
    """
    # Use null-byte separator since commit messages can contain
    # arbitrary text including newlines.
    rc, out, _ = _run_git(
        repo, "log", "-1",
        "--format=%h%x00%s%x00%B%x00%aI",
        branch,
    )
    if rc != 0 or not out:
        return None
    parts = out.rstrip("\n").split("\x00")
    if len(parts) < 4:
        return None
    return parts[0], parts[1], parts[2], parts[3]


def _diff_stat(
    repo: Path, branch: str, base: str = DEFAULT_BASE_BRANCH,
) -> tuple[int, int, int]:
    """Return (files_changed, insertions, deletions) for branch vs
    base. Defensive: any failure returns (0, 0, 0)."""
    rc, out, _ = _run_git(
        repo, "diff", "--shortstat", f"{base}...{branch}",
    )
    if rc != 0 or not out:
        return (0, 0, 0)
    # Format: " 3 files changed, 100 insertions(+), 20 deletions(-)"
    files_m = re.search(r"(\d+) file", out)
    ins_m = re.search(r"(\d+) insertion", out)
    del_m = re.search(r"(\d+) deletion", out)
    return (
        int(files_m.group(1)) if files_m else 0,
        int(ins_m.group(1)) if ins_m else 0,
        int(del_m.group(1)) if del_m else 0,
    )


def _read_file_at_branch(
    repo: Path, branch: str, path: str,
) -> str | None:
    """Read a file at HEAD of the given branch via ``git show``.
    Returns None when the file doesn't exist there."""
    rc, out, _ = _run_git(repo, "show", f"{branch}:{path}")
    if rc != 0:
        return None
    return out


def _find_cycle_report(
    repo: Path, branch: str, cycle_id: str,
) -> tuple[str | None, str | None]:
    """Locate + read the cycle report for the given branch.
    Returns (path, content) on hit, (None, None) when nothing matches.
    """
    candidates = [
        c.format(cycle_id=cycle_id) if "{cycle_id}" in c else c
        for c in CYCLE_REPORT_CANDIDATES
    ]
    for candidate in candidates:
        content = _read_file_at_branch(repo, branch, candidate)
        if content is not None:
            return candidate, content
    return None, None


def _parse_requested_tools(report: str | None) -> list[dict]:
    """Parse a requested_tools block from the cycle report's
    front-matter or body. Conservative: looks for a simple
    yaml-block under 'requested_tools:' header. Returns an empty
    list on any parse error (E4 doesn't action on these — it just
    surfaces them; E5 will validate stricter).
    """
    if not report:
        return []
    try:
        import yaml
    except ImportError:
        return []
    # Look for a yaml block delimited by ```yaml fences.
    fence_match = re.search(
        r"```yaml\s*\n(.*?)\n```",
        report,
        flags=re.DOTALL,
    )
    if not fence_match:
        return []
    try:
        data = yaml.safe_load(fence_match.group(1))
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("requested_tools")
    if not isinstance(raw, list):
        return []
    return [r for r in raw if isinstance(r, dict)]


def _derive_status(
    has_report: bool, report_content: str | None, branch: str,
    repo: Path,
) -> str:
    """Cycle-status heuristic. Cheap and string-based to avoid
    per-cycle git rev-list calls."""
    # Was the branch already merged into main?
    rc, out, _ = _run_git(
        repo, "branch", "--contains", branch, "--list", DEFAULT_BASE_BRANCH,
    )
    if rc == 0 and out.strip():
        return "merged"
    if not has_report:
        return "pending"
    text = (report_content or "").lower()
    if "test_outcome: passed" in text or "tests passed" in text:
        return "passed"
    if "test_outcome: failed" in text or "tests failed" in text:
        return "failed"
    return "ready"


def _resolve_workspace_path(request: Request) -> Path | None:
    """Pull the experimenter workspace path off DaemonSettings.
    None when not configured."""
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        return None
    p = getattr(settings, "experimenter_workspace_path", None)
    if p is None:
        return None
    p = Path(p).expanduser()
    if not p.exists() or not (p / ".git").exists():
        return None
    return p


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get(
    "/{instance_id}/cycles",
    response_model=CycleListOut,
    dependencies=[Depends(require_api_token)],
)
def list_cycles(
    instance_id: str,
    request: Request,
    registry: Registry = Depends(get_registry),
) -> CycleListOut:
    """List Smith's branch-isolated cycles. Cheap O(branches)
    git operations per call. Suitable for ~5s refresh tick from
    the chat pane.
    """
    # Validate the agent exists and IS the experimenter — listing
    # cycles for any other agent is meaningless. We don't enforce
    # by role (test agents could be experimenter-equivalent), just
    # confirm the instance_id is real.
    try:
        registry.get_agent(instance_id)
    except UnknownAgentError:
        raise HTTPException(
            status_code=404,
            detail=f"agent {instance_id!r} not found",
        )

    repo = _resolve_workspace_path(request)
    if repo is None:
        return CycleListOut(
            cycles=[],
            workspace_path=None,
            workspace_available=False,
        )

    summaries: list[CycleSummary] = []
    for cycle_id, branch in _list_cycle_branches(repo):
        head = _head_info(repo, branch)
        if head is None:
            # Branch exists but log failed — skip rather than 500
            continue
        sha, msg_first, _full_msg, ts = head
        files, ins, dels = _diff_stat(repo, branch)
        report_path, report_content = _find_cycle_report(
            repo, branch, cycle_id,
        )
        status = _derive_status(
            has_report=report_path is not None,
            report_content=report_content,
            branch=branch,
            repo=repo,
        )
        summaries.append(CycleSummary(
            cycle_id=cycle_id,
            branch=branch,
            head_sha=sha[:12],
            head_message=msg_first,
            head_timestamp=ts,
            files_changed=files,
            insertions=ins,
            deletions=dels,
            has_cycle_report=report_path is not None,
            status=status,  # type: ignore[arg-type]
        ))

    return CycleListOut(
        cycles=summaries,
        workspace_path=str(repo),
        workspace_available=True,
    )


@router.get(
    "/{instance_id}/cycles/{cycle_id}",
    response_model=CycleDetail,
    dependencies=[Depends(require_api_token)],
)
def get_cycle_detail(
    instance_id: str,
    cycle_id: str,
    request: Request,
    registry: Registry = Depends(get_registry),
) -> CycleDetail:
    """Detail view for one cycle. Full diff (size-capped at 200KB)
    + full commit message + cycle report + parsed
    requested_tools."""
    try:
        registry.get_agent(instance_id)
    except UnknownAgentError:
        raise HTTPException(
            status_code=404,
            detail=f"agent {instance_id!r} not found",
        )

    repo = _resolve_workspace_path(request)
    if repo is None:
        raise HTTPException(
            status_code=404,
            detail="experimenter workspace not provisioned",
        )

    # Validate the cycle_id matches a real branch — refuse
    # arbitrary path traversal. Pattern: cycle-N
    if not re.match(r"^cycle-\d+$", cycle_id):
        raise HTTPException(
            status_code=400,
            detail=f"invalid cycle_id {cycle_id!r}; expected 'cycle-N'",
        )
    branch = f"experimenter/{cycle_id}"

    # Confirm branch exists.
    rc, _, _ = _run_git(repo, "rev-parse", "--verify", branch)
    if rc != 0:
        raise HTTPException(
            status_code=404,
            detail=f"branch {branch!r} not found in workspace",
        )

    head = _head_info(repo, branch)
    if head is None:
        raise HTTPException(
            status_code=500,
            detail=f"failed to read HEAD of {branch!r}",
        )
    sha, msg_first, full_msg, ts = head
    files, ins, dels = _diff_stat(repo, branch)

    # Full diff with truncation guard.
    rc, diff_text, _ = _run_git(
        repo, "diff", f"{DEFAULT_BASE_BRANCH}...{branch}",
    )
    if rc != 0:
        diff_text = ""
    diff_bytes = diff_text.encode("utf-8")
    truncated = False
    if len(diff_bytes) > DEFAULT_MAX_DIFF_BYTES:
        # Cut at the last newline before the cap so the truncated
        # output stays parseable.
        clipped = diff_bytes[:DEFAULT_MAX_DIFF_BYTES]
        last_nl = clipped.rfind(b"\n")
        if last_nl > 0:
            clipped = clipped[:last_nl]
        diff_text = clipped.decode("utf-8", errors="replace") + (
            "\n\n... (diff truncated; see workspace for full)\n"
        )
        truncated = True

    report_path, report_content = _find_cycle_report(
        repo, branch, cycle_id,
    )
    status = _derive_status(
        has_report=report_path is not None,
        report_content=report_content,
        branch=branch,
        repo=repo,
    )
    requested = _parse_requested_tools(report_content)

    return CycleDetail(
        cycle_id=cycle_id,
        branch=branch,
        head_sha=sha[:12],
        head_message=msg_first,
        head_timestamp=ts,
        files_changed=files,
        insertions=ins,
        deletions=dels,
        status=status,  # type: ignore[arg-type]
        full_commit_message=full_msg,
        diff=diff_text,
        diff_truncated=truncated,
        cycle_report_path=report_path,
        cycle_report_content=report_content,
        requested_tools=requested,
    )
