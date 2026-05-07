"""``/agents/{instance_id}/cycles`` — ADR-0056 E4 + E5.

Display-mode read surface for Smith's branch-isolated cycles
plus the E5 decision write surface. Reads the experimenter
workspace via git subprocess (no GitPython dep) and surfaces
cycle metadata to the chat tab.

Three endpoints:

  GET /agents/{instance_id}/cycles
    List view. One row per branch matching experimenter/cycle-*.
    Cheap: one rev-parse + one diff-stat per branch. Suitable
    for a 5-second refresh tick on the chat pane.

  GET /agents/{instance_id}/cycles/{cycle_id}
    Detail view. Full diff (size-capped) + full commit message
    + cycle report content if present + parsed requested_tools.
    More expensive — fired on row expand only.

  POST /agents/{instance_id}/cycles/{cycle_id}/decision
    E5 — operator decision write. action ∈ {approve, deny,
    counter}.
      approve: git merge --no-ff in the workspace's main
        branch. On merge conflict, aborts and returns 409 so
        the operator resolves manually. Emits
        experimenter_cycle_decision audit event with
        action=approve.
      deny: deletes the branch (git branch -D). Emits the
        same event with action=deny.
      counter: writes a feedback note to Smith's memory
        (deferred — ships as a memory_write call in a
        follow-up). Emits the same event with
        action=counter + note in event_data.

Decision endpoint is the only WRITE surface. requires_writes_enabled
gate applies. Per-cycle action is idempotent on deny + counter;
approve is idempotent only when the merge is fast-forwardable
(otherwise the second call returns 'already merged').

Per ADR-0056 D5: cycle_id is the branch name without the
'experimenter/' prefix.

Per ADR-0001 D2: read endpoints touch no identity. Decision
endpoint emits an audit event but does NOT mutate Smith's
constitution_hash or DNA. The merge into main is a state
mutation in the workspace clone, not the kernel registry.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import threading
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.daemon.deps import (
    get_audit_chain,
    get_registry,
    get_write_lock,
    require_api_token,
    require_writes_enabled,
)
from forest_soul_forge.daemon.schemas import (
    CycleDetail,
    CycleListOut,
    CycleSummary,
)
from forest_soul_forge.registry import Registry
from forest_soul_forge.registry.registry import UnknownAgentError


# ---------------------------------------------------------------------------
# E5 — Decision request/response shapes (inline since they're scoped
# to this router and don't need to live in schemas/__init__.py).
# ---------------------------------------------------------------------------
DecisionAction = Literal["approve", "deny", "counter"]


class CycleDecisionRequest(BaseModel):
    """Body for POST /agents/{instance_id}/cycles/{cycle_id}/decision."""

    action: DecisionAction
    note: str | None = Field(
        default=None,
        max_length=2000,
        description=(
            "Optional operator note. For action='counter' this "
            "becomes Smith's feedback message for the next "
            "explore tick. For approve/deny it lands in the "
            "audit event for after-the-fact context."
        ),
    )
    delete_branch: bool = Field(
        default=False,
        description=(
            "For action='deny' only — also delete the branch "
            "(git branch -D experimenter/cycle-N) after tagging "
            "the outcome. Default false: deny tags outcome but "
            "keeps the branch around for forensics."
        ),
    )


class CycleDecisionResponse(BaseModel):
    """Response from POST /agents/{id}/cycles/{cycle_id}/decision."""

    ok: bool
    action: DecisionAction
    cycle_id: str
    branch: str
    audit_seq: int
    merge_commit_sha: str | None = Field(
        default=None,
        description=(
            "For action='approve' only — the SHA of the merge "
            "commit on main. Operator pushes to origin to "
            "publish."
        ),
    )
    branch_deleted: bool = Field(
        default=False,
        description="True when action='deny' + delete_branch=true succeeded.",
    )
    detail: str = Field(
        default="",
        description="Human-readable summary of what happened.",
    )


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


# ---------------------------------------------------------------------------
# E5 — Decision endpoint. Approve (merge), Deny (tag outcome,
# optionally delete branch), Counter (record note for next tick).
# ---------------------------------------------------------------------------
@router.post(
    "/{instance_id}/cycles/{cycle_id}/decision",
    response_model=CycleDecisionResponse,
    dependencies=[
        Depends(require_writes_enabled),
        Depends(require_api_token),
    ],
)
def decide_cycle(
    instance_id: str,
    cycle_id: str,
    body: CycleDecisionRequest,
    request: Request,
    registry: Registry = Depends(get_registry),
    audit: AuditChain = Depends(get_audit_chain),
    write_lock: threading.Lock = Depends(get_write_lock),
) -> CycleDecisionResponse:
    """ADR-0056 E5 — operator decision on a Smith cycle.

    Three action modes:

      approve — runs ``git merge --no-ff experimenter/cycle-N``
        in the workspace's main branch. On clean merge, returns
        the merge commit SHA. On conflict, aborts the merge
        cleanly and returns 409 with a detail message; the
        operator resolves manually then re-fires this endpoint.
      deny — tags the cycle as denied via the audit event;
        optionally deletes the branch (delete_branch=true).
      counter — records the operator's note for routing into
        Smith's next explore-mode tick. v0.1 just emits the
        audit event with the note; the explore-prompt
        machinery picks it up automatically when the
        scheduler fires (Smith's prompt instructs reading
        recent audit events for operator feedback).

    All three actions emit ONE ``experimenter_cycle_decision``
    audit event. The action field discriminates; the rest of
    event_data carries cycle_id, branch, head_sha, optional
    note, and (for approve) merge_commit_sha.

    Per ADR-0056 D3: the merge happens in the experimenter
    workspace clone (~/.fsf/experimenter-workspace/), NOT the
    operator's main work tree. After a successful approve, the
    operator pushes to origin from the workspace if they want
    to publish:
        cd ~/.fsf/experimenter-workspace/Forest-Soul-Forge
        git push origin main
    """
    # Validate the agent exists.
    try:
        agent = registry.get_agent(instance_id)
    except UnknownAgentError:
        raise HTTPException(
            status_code=404,
            detail=f"agent {instance_id!r} not found",
        )

    # Validate cycle_id pattern (path-traversal defense).
    import re
    if not re.match(r"^cycle-\d+$", cycle_id):
        raise HTTPException(
            status_code=400,
            detail=f"invalid cycle_id {cycle_id!r}; expected 'cycle-N'",
        )
    branch = f"experimenter/{cycle_id}"

    # Resolve workspace.
    repo = _resolve_workspace_path(request)
    if repo is None:
        raise HTTPException(
            status_code=404,
            detail="experimenter workspace not provisioned",
        )

    # Confirm branch exists.
    rc, _, _ = _run_git(repo, "rev-parse", "--verify", branch)
    if rc != 0:
        raise HTTPException(
            status_code=404,
            detail=f"branch {branch!r} not found in workspace",
        )

    # Capture HEAD sha for audit metadata.
    rc, head_sha_full, _ = _run_git(
        repo, "rev-parse", branch,
    )
    head_sha = (head_sha_full or "").strip()[:12] or "unknown"

    # All write paths take the daemon write lock so concurrent
    # decision calls + concurrent audit emissions are
    # serialized.
    with write_lock:
        merge_commit_sha: str | None = None
        branch_deleted = False
        detail_msg = ""

        if body.action == "approve":
            # Switch to main, merge --no-ff. On conflict, abort
            # and return 409.
            rc, _, err = _run_git(repo, "checkout", DEFAULT_BASE_BRANCH)
            if rc != 0:
                raise HTTPException(
                    status_code=500,
                    detail=f"failed to checkout main: {err.strip()[:200]}",
                )
            merge_msg = (
                f"Merge experimenter/{cycle_id} (operator-approved)\n"
                f"\n"
                f"{(body.note or '')}".rstrip()
            )
            rc, mout, merr = _run_git(
                repo, "merge", "--no-ff", branch,
                "-m", merge_msg,
            )
            if rc != 0:
                # Abort to leave main clean.
                _run_git(repo, "merge", "--abort")
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"merge of {branch!r} into main produced a "
                        f"conflict; merge aborted. Resolve manually "
                        f"in the workspace and re-fire the decision. "
                        f"git output: {(mout + merr).strip()[:300]}"
                    ),
                )
            # Capture the merge commit SHA.
            rc, sha_out, _ = _run_git(repo, "rev-parse", "HEAD")
            if rc == 0:
                merge_commit_sha = sha_out.strip()[:12]
            detail_msg = (
                f"approved: merged {branch} into main"
                + (f" as {merge_commit_sha}" if merge_commit_sha else "")
                + ". Push to origin from the workspace to publish."
            )

        elif body.action == "deny":
            if body.delete_branch:
                rc, _, derr = _run_git(repo, "branch", "-D", branch)
                if rc == 0:
                    branch_deleted = True
                    detail_msg = (
                        f"denied: branch {branch} deleted from workspace."
                    )
                else:
                    detail_msg = (
                        f"denied: tag recorded but branch delete "
                        f"failed ({derr.strip()[:120]}). Branch "
                        f"remains for forensics."
                    )
            else:
                detail_msg = (
                    f"denied: outcome tagged; branch {branch} "
                    f"preserved for forensics."
                )

        elif body.action == "counter":
            detail_msg = (
                "counter-proposed: note recorded in audit chain. "
                "Smith picks up operator feedback on the next "
                "explore-mode tick (memory_recall surfaces recent "
                "audit events with operator notes)."
            )

        # Emit the unified audit event.
        try:
            entry = audit.append(
                "experimenter_cycle_decision",
                {
                    "instance_id":      instance_id,
                    "cycle_id":         cycle_id,
                    "branch":           branch,
                    "head_sha":         head_sha,
                    "action":           body.action,
                    "note":             body.note,
                    "merge_commit_sha": merge_commit_sha,
                    "branch_deleted":   branch_deleted,
                },
                agent_dna=agent.dna,
            )
            audit_seq = entry.seq
        except Exception as e:  # noqa: BLE001 — defensive
            # Audit emit failure shouldn't undo a successful merge,
            # but the operator should know.
            raise HTTPException(
                status_code=500,
                detail=(
                    f"action={body.action} side-effects landed but "
                    f"audit emission failed: {type(e).__name__}: {e}. "
                    f"Verify chain integrity via "
                    f"audit_chain_verify.v1 before proceeding."
                ),
            )

    return CycleDecisionResponse(
        ok=True,
        action=body.action,
        cycle_id=cycle_id,
        branch=branch,
        audit_seq=audit_seq,
        merge_commit_sha=merge_commit_sha,
        branch_deleted=branch_deleted,
        detail=detail_msg,
    )
