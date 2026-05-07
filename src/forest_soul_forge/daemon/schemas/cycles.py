"""ADR-0056 E4 (Burst 190) — display-mode cycles read models.

Surfaces Smith's branch-isolated work cycles to the chat tab's
display-mode pane. A "cycle" maps to one branch in the
experimenter workspace matching the ``experimenter/cycle-N``
naming convention. The router reads git via subprocess (no
GitPython dep) so the daemon's footprint stays minimal.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# Status semantics:
#   pending  — branch exists; no cycle report yet (Smith is mid-cycle)
#   ready    — branch + cycle report present + tests not yet validated
#   passed   — tests passed (cycle report mentions test_outcome=passed)
#   failed   — tests failed (cycle report mentions test_outcome=failed)
#   merged   — branch already merged into main (no longer pending review)
CycleStatus = Literal["pending", "ready", "passed", "failed", "merged"]


class CycleSummary(BaseModel):
    """One row in the Cycles list view. Cheap to compute (one
    git rev-parse + one diff-stat per branch)."""

    cycle_id: str = Field(
        ...,
        description=(
            "Branch name without the 'experimenter/' prefix — "
            "e.g. 'cycle-1', 'cycle-2'. Stable across daemon "
            "restarts; the chat pane uses this as the React-style "
            "key for the row."
        ),
    )
    branch: str = Field(
        ...,
        description="Full branch name, e.g. 'experimenter/cycle-1'.",
    )
    head_sha: str = Field(
        ...,
        description="HEAD commit SHA of the branch (short, 12 chars).",
    )
    head_message: str = Field(
        ...,
        description=(
            "First line of the HEAD commit message. The full "
            "message is available in the detail endpoint."
        ),
    )
    head_timestamp: str = Field(
        ...,
        description="ISO timestamp of the HEAD commit author date.",
    )
    files_changed: int = Field(
        ge=0,
        description="diff-stat: files-changed count vs main.",
    )
    insertions: int = Field(
        ge=0,
        description="diff-stat: lines inserted vs main.",
    )
    deletions: int = Field(
        ge=0,
        description="diff-stat: lines deleted vs main.",
    )
    has_cycle_report: bool = Field(
        ...,
        description=(
            "True when the branch's HEAD tree contains "
            "CYCLE_REPORT.md or docs/cycles/cycle-N.md. The UI "
            "uses this to badge 'ready for review' rows."
        ),
    )
    status: CycleStatus = Field(
        ...,
        description=(
            "Cycle status derived from the branch state + the "
            "cycle report's test_outcome field if present."
        ),
    )


class CycleDetail(BaseModel):
    """Full cycle data for the expand view. Reads more git data
    than CycleSummary — full diff, full commit message, cycle
    report content."""

    cycle_id: str
    branch: str
    head_sha: str
    head_message: str
    head_timestamp: str
    files_changed: int
    insertions: int
    deletions: int
    status: CycleStatus

    full_commit_message: str = Field(
        ...,
        description="The complete commit message body, not just the first line.",
    )
    diff: str = Field(
        ...,
        description=(
            "git diff main..branch — full unified diff. May be "
            "truncated on the daemon side at "
            "FSF_CYCLES_MAX_DIFF_BYTES (default 200KB) to keep "
            "the response payload bounded."
        ),
    )
    diff_truncated: bool = Field(
        default=False,
        description="True when the diff was truncated for size.",
    )
    cycle_report_path: str | None = Field(
        default=None,
        description=(
            "Repo-relative path to the cycle report inside the "
            "branch (CYCLE_REPORT.md or docs/cycles/cycle-N.md). "
            "None when no report file exists."
        ),
    )
    cycle_report_content: str | None = Field(
        default=None,
        description="Raw markdown content of the cycle report.",
    )
    requested_tools: list[dict] = Field(
        default_factory=list,
        description=(
            "Parsed `requested_tools:` block from the cycle "
            "report's frontmatter, when present. Each dict "
            "matches ADR-0056 D6's schema (name, version, "
            "side_effects, reason, files). E5 will surface "
            "these as approval checkboxes; E4 just lists them."
        ),
    )


class CycleListOut(BaseModel):
    """Response shape for ``GET /agents/{id}/cycles``."""

    cycles: list[CycleSummary]
    workspace_path: str | None = Field(
        default=None,
        description=(
            "Absolute path to the experimenter workspace. None "
            "when the workspace isn't provisioned (cycles list "
            "is empty in that case)."
        ),
    )
    workspace_available: bool = Field(
        ...,
        description=(
            "True when the workspace exists + is a git repo. "
            "Frontend uses this to distinguish 'no cycles yet' "
            "(workspace_available=true, cycles=[]) from 'Smith "
            "isn't provisioned' (workspace_available=false)."
        ),
    )
