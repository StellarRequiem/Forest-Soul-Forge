"""``/self-improve/...`` — operator surface for the self-improvement
audit findings produced by ``scripts/self_improve.py``.

Each run of the harness writes ``docs/self-improvement/report-<ts>.json``
with the audit findings, the auto-fix plan, the FIX outcomes, and a
session/circuit-breaker block. This router lets the frontend's
Approvals page list those findings, group them by severity/category,
and persist per-finding operator decisions (implement / audit /
reject) to a sidecar JSON so the choices survive across audit re-runs.

The sidecar is content-addressed by ``finding_id`` (sha1 of
``kind|details.id|summary``) so a finding that recurs in a later
report keeps its decision automatically — re-running the harness
doesn't reset the operator's queue.

Endpoints:

* ``GET /self-improve/reports``
  List available reports newest-first with a small summary block
  (timestamp, branch, finding count, pytest pass/fail counts).
* ``GET /self-improve/findings[?report=<filename>]``
  Findings from the requested report (default: latest), normalized
  with stable ``finding_id`` and merged with the matching sidecar
  decision (or ``pending`` if no decision yet).
* ``POST /self-improve/findings/{finding_id}/decision``
  Body ``{status, operator_id, note?, report}``. ``status`` is one of
  ``pending | approved_for_fix | under_audit | implemented | rejected``.
  Writes the decision to ``docs/self-improvement/decisions.json`` under
  the daemon's write lock.

Read endpoints are ungated (same posture as ``/audit/tail`` —
operator-facing, no agent identity, no secrets). The POST honours
``allow_write_endpoints`` + ``api_token`` so a read-only daemon refuses
mutations cleanly.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from forest_soul_forge.daemon.deps import (
    get_write_lock,
    require_api_token,
    require_writes_enabled,
)


router = APIRouter(tags=["self-improve"], prefix="/api/self-improve")


# Path defaults — relative to the daemon's CWD, matching the
# audit_chain_path convention in daemon/config.py. The harness writes
# reports here; the sidecar with operator decisions sits alongside.
REPORT_DIR = Path("docs/self-improvement")
DECISIONS_FILE = REPORT_DIR / "decisions.json"

# Status vocabulary — keep tight so the UI can render distinct affordances
# and the harness can later read this to gate auto-fix attempts (only
# work on `approved_for_fix`, never on `rejected` or `under_audit`).
VALID_STATUSES = frozenset({
    "pending",
    "approved_for_fix",
    "under_audit",
    "implemented",
    "rejected",
})

# Report filename pattern — `report-<iso-ish>.json`. The .md siblings are
# operator-readable companions, not consumed here.
REPORT_RE = re.compile(r"^report-(\d{4}-\d{2}-\d{2}-\d{6})\.json$")


def _override_report_dir() -> Path:
    """Allow tests to redirect the report dir via env var without
    monkeypatching this module. Mirrors FSF_AUDIT_CHAIN_PATH's spirit.
    """
    override = os.environ.get("FSF_SELF_IMPROVE_DIR")
    return Path(override) if override else REPORT_DIR


def _decisions_path() -> Path:
    return _override_report_dir() / "decisions.json"


def _finding_id(finding: dict[str, Any]) -> str:
    """Stable 16-hex-char id for one finding.

    Hashes ``kind|details.id|summary`` — those three fields together are
    sufficient to disambiguate every finding the current harness emits
    (test failures use details.id, drift/config findings use summary
    alone, the kind disambiguates the source). SHA1 not for security; we
    want a short collision-resistant slug that survives re-runs.
    """
    kind = str(finding.get("kind", ""))
    details = finding.get("details") or {}
    detail_id = ""
    if isinstance(details, dict):
        detail_id = str(details.get("id", ""))
    summary = str(finding.get("summary", ""))
    key = f"{kind}|{detail_id}|{summary}".encode("utf-8")
    return hashlib.sha1(key).hexdigest()[:16]


def _category(finding: dict[str, Any]) -> str:
    """Human-friendly category bucket for grouping in the UI.

    The harness emits ``kind`` values like ``test_failure``,
    ``test_failure_group``, ``config_drift``, ``ruff_lint``, etc.
    We collapse the test_failure / test_failure_group pair so the
    UI groups them together — the operator cares about the test
    target, not whether the report happened to grouping-roll them.
    """
    kind = str(finding.get("kind", "")).lower()
    if kind.startswith("test_failure"):
        return "tests"
    if "drift" in kind:
        return "drift"
    if "lint" in kind or "ruff" in kind:
        return "lint"
    if "security" in kind or "audit_chain" in kind:
        return "security"
    return kind or "other"


def _list_reports(report_dir: Path) -> list[Path]:
    """All report-*.json files, newest filename first.

    Filenames are ISO-ish (YYYY-MM-DD-HHMMSS) so lexicographic sort
    matches chronological order without parsing.
    """
    if not report_dir.exists():
        return []
    paths = [p for p in report_dir.iterdir()
             if p.is_file() and REPORT_RE.match(p.name)]
    paths.sort(key=lambda p: p.name, reverse=True)
    return paths


def _load_report(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"could not read report {path.name}: {e}",
        ) from e


def _summarize_report(path: Path, body: dict[str, Any]) -> dict[str, Any]:
    audit = body.get("audit") or {}
    findings = audit.get("findings") or []
    pytest = audit.get("pytest_summary") or {}
    plan = body.get("plan") or {}
    session = body.get("session") or {}
    return {
        "filename": path.name,
        "timestamp": body.get("timestamp", ""),
        "branch": body.get("branch", ""),
        "finding_count": len(findings),
        "auto_fix_count": len(plan.get("auto_fix") or []),
        "flagged_count": len(plan.get("flagged") or []),
        "pytest_passed": pytest.get("passed", 0),
        "pytest_failed": pytest.get("failed", 0),
        "pytest_errors": pytest.get("errors", 0),
        "pytest_skipped": pytest.get("skipped", 0),
        "aborted": bool(session.get("aborted", False)),
        "abort_reason": session.get("abort_reason", ""),
    }


def _load_decisions() -> dict[str, dict[str, Any]]:
    path = _decisions_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # A corrupted sidecar shouldn't take down the read path —
        # the operator can re-decide. The POST handler rewrites the
        # file atomically anyway.
        return {}
    decisions = raw.get("decisions") if isinstance(raw, dict) else None
    return decisions if isinstance(decisions, dict) else {}


def _write_decisions(decisions: dict[str, dict[str, Any]]) -> None:
    path = _decisions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file then rename so a crash mid-write can't leave
    # a partial JSON on disk.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps({"decisions": decisions}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _resolve_report(
    report_dir: Path, requested: str | None,
) -> Path:
    reports = _list_reports(report_dir)
    if not reports:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "no self-improvement reports found in "
                f"{report_dir}. Run scripts/self_improve.py to generate "
                "one."
            ),
        )
    if not requested:
        return reports[0]
    # Reject path traversal — only filenames in the report dir.
    if "/" in requested or "\\" in requested or ".." in requested:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="report must be a bare filename, not a path",
        )
    if not REPORT_RE.match(requested):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"report filename does not match expected pattern: {requested}",
        )
    target = report_dir / requested
    if not target.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"report not found: {requested}",
        )
    return target


def _index_plan(report: dict[str, Any]) -> tuple[set[str], set[str]]:
    plan = report.get("plan") or {}
    auto_ids = {_finding_id(f) for f in (plan.get("auto_fix") or [])}
    flagged_ids = {_finding_id(f) for f in (plan.get("flagged") or [])}
    return auto_ids, flagged_ids


def _index_outcomes(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map finding_id -> outcome (status + changed_files + diff/error)."""
    out: dict[str, dict[str, Any]] = {}
    for o in report.get("outcomes") or []:
        if not isinstance(o, dict):
            continue
        f = o.get("finding")
        if not isinstance(f, dict):
            continue
        fid = _finding_id(f)
        out[fid] = {
            "status": o.get("status", ""),
            "changed_files": o.get("changed_files") or [],
            "diff": o.get("diff", ""),
            "error": o.get("error", ""),
        }
    return out


def _affected_files(finding: dict[str, Any]) -> list[str]:
    """Best-effort extraction of file paths a finding refers to.

    For test failures the path lives at the front of ``details.id``
    (``tests/conformance/...py::test_...``). Drift/lint findings carry
    a ``details.files`` list. Fallback: empty list.
    """
    details = finding.get("details")
    if not isinstance(details, dict):
        return []
    explicit = details.get("files")
    if isinstance(explicit, list):
        return [str(x) for x in explicit if x]
    tid = details.get("id")
    if isinstance(tid, str) and "::" in tid:
        return [tid.split("::", 1)[0]]
    if isinstance(tid, str) and tid.endswith(".py"):
        return [tid]
    return []


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/reports")
def list_reports() -> dict[str, Any]:
    """List available self-improvement reports newest first."""
    report_dir = _override_report_dir()
    paths = _list_reports(report_dir)
    reports = []
    for p in paths:
        try:
            body = _load_report(p)
        except HTTPException:
            # A single broken report shouldn't hide the rest. Surface
            # the filename with an error marker.
            reports.append({
                "filename": p.name,
                "timestamp": "",
                "branch": "",
                "error": "could not parse",
            })
            continue
        reports.append(_summarize_report(p, body))
    return {"count": len(reports), "reports": reports}


@router.get("/findings")
def list_findings(
    report: str | None = Query(default=None, description=(
        "Report filename to load. Default: most recent."
    )),
) -> dict[str, Any]:
    """Return one report's findings, normalized + decision-annotated."""
    report_dir = _override_report_dir()
    target = _resolve_report(report_dir, report)
    body = _load_report(target)
    auto_ids, flagged_ids = _index_plan(body)
    outcomes = _index_outcomes(body)
    decisions = _load_decisions()

    audit = body.get("audit") or {}
    raw_findings = audit.get("findings") or []
    out: list[dict[str, Any]] = []
    by_category: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for f in raw_findings:
        if not isinstance(f, dict):
            continue
        fid = _finding_id(f)
        cat = _category(f)
        sev = str(f.get("severity", ""))
        decision = decisions.get(fid) or {}
        eff_status = decision.get("status", "pending")
        by_category[cat] = by_category.get(cat, 0) + 1
        by_severity[sev] = by_severity.get(sev, 0) + 1
        by_status[eff_status] = by_status.get(eff_status, 0) + 1
        out.append({
            "finding_id": fid,
            "kind": f.get("kind", ""),
            "category": cat,
            "severity": sev,
            "summary": f.get("summary", ""),
            "source": f.get("source", ""),
            "details": f.get("details") or {},
            "affected_files": _affected_files(f),
            "in_auto_fix_plan": fid in auto_ids,
            "in_flagged_plan": fid in flagged_ids,
            "outcome": outcomes.get(fid),
            "decision": {
                "status": eff_status,
                "decided_by": decision.get("decided_by", ""),
                "decided_at": decision.get("decided_at", ""),
                "note": decision.get("note", ""),
                "report": decision.get("report", ""),
            },
        })

    return {
        "report": _summarize_report(target, body),
        "totals": {
            "findings": len(out),
            "by_category": by_category,
            "by_severity": by_severity,
            "by_status": by_status,
        },
        "findings": out,
    }


@router.post(
    "/findings/{finding_id}/decision",
    dependencies=[
        Depends(require_writes_enabled),
        Depends(require_api_token),
    ],
)
def set_decision(
    finding_id: str,
    body: dict[str, Any] = Body(...),
    write_lock=Depends(get_write_lock),
) -> dict[str, Any]:
    """Persist an operator decision for one finding.

    Body shape::

        {
          "status": "approved_for_fix",
          "operator_id": "alex",       # required, non-empty
          "note": "optional",
          "report": "report-...json"   # required so the sidecar
                                       # remembers where the id came from
        }

    The handler is idempotent: re-posting the same status is a no-op on
    the file (timestamp updates so we can see when the operator
    re-acknowledged the call).
    """
    if not re.fullmatch(r"[0-9a-f]{16}", finding_id or ""):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="finding_id must be a 16-char hex slug",
        )
    new_status = str(body.get("status", "")).strip()
    if new_status not in VALID_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"invalid status {new_status!r}; expected one of "
                f"{sorted(VALID_STATUSES)}"
            ),
        )
    operator_id = str(body.get("operator_id", "")).strip()
    if not operator_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="operator_id required",
        )
    note = str(body.get("note", "")).strip()
    report = str(body.get("report", "")).strip()
    if not report or not REPORT_RE.match(report):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="report (the source report filename) required",
        )

    # Confirm the finding_id actually exists in the named report — this
    # catches typos and stale clients pointing at a stale finding list.
    report_dir = _override_report_dir()
    target = _resolve_report(report_dir, report)
    report_body = _load_report(target)
    audit = report_body.get("audit") or {}
    ids = {_finding_id(f) for f in (audit.get("findings") or [])
           if isinstance(f, dict)}
    if finding_id not in ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"finding_id {finding_id} not found in report {report}",
        )

    decided_at = datetime.now(timezone.utc).astimezone().isoformat(
        timespec="seconds",
    )

    # Carry the summary forward into the sidecar so a future reader
    # (or a manual sidecar inspection) doesn't need to re-load the
    # report to know what they decided.
    summary = ""
    kind = ""
    for f in audit.get("findings") or []:
        if isinstance(f, dict) and _finding_id(f) == finding_id:
            summary = str(f.get("summary", ""))
            kind = str(f.get("kind", ""))
            break

    with write_lock:
        decisions = _load_decisions()
        decisions[finding_id] = {
            "status": new_status,
            "decided_by": operator_id,
            "decided_at": decided_at,
            "note": note,
            "report": report,
            "summary": summary,
            "kind": kind,
        }
        _write_decisions(decisions)

    return {
        "finding_id": finding_id,
        "decision": decisions[finding_id],
    }
