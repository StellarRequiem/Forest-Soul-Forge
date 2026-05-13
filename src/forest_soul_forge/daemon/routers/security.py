"""``/security/*`` — ADR-0062 T6 operator-facing read surface.

Closes ADR-0062. Mirrors the ADR-0063 T7 ``/reality-anchor/*``
shape (B256). Five endpoints expose the supply-chain scanner
substrate to the operator without giving the UI any
action-taking power:

  - **GET  /security/status**
    Combined summary card: IoC rule count, catalog errors,
    recent refuse/allow counts (last 24h), quarantined-dir
    count. Drives the pane's hero block.

  - **GET  /security/iocs**
    The full IoC catalog (config/security_iocs.yaml) with
    id / severity / pattern / applies_to / rationale /
    references for every rule. Read-only per ADR-0062 D1
    (operator edits the YAML directly).

  - **GET  /security/recent-scans**
    Last N ``agent_security_scan_completed`` audit events
    (install + forge surfaces). Drives the events timeline.

  - **GET  /security/quarantined**
    List of staged dirs that contain REJECTED.md (forge-stage
    scanner quarantine marker from T5). One row per
    quarantined proposal.

  - **POST /security/reload**
    Hot-reload the IoC catalog (security_scan.v1 reloads on
    each call today; this endpoint is a no-op that returns
    the post-reload counts for the frontend's convenience).

All endpoints require the API token; none require writes-
enabled. This is the monitoring surface, not the gate itself.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from forest_soul_forge.daemon.deps import (
    get_audit_chain,
    get_settings,
    require_api_token,
)


router = APIRouter(prefix="/security", tags=["security"])


# ---- constants ------------------------------------------------------------

#: Default catalog path, matched by security_scan.v1's own default.
DEFAULT_IOC_CATALOG = Path("config/security_iocs.yaml")

#: Where the forge engines stage proposals. The status endpoint walks
#: these two roots looking for REJECTED.md markers.
DEFAULT_STAGED_ROOTS = (
    Path("data/forge/skills/staged"),
    Path("data/forge/tools/staged"),
)


# ---- helpers --------------------------------------------------------------


def _load_iocs(path: Path) -> tuple[list[dict[str, Any]], list[str], int]:
    """Load + lightly normalize the IoC catalog. Returns
    (rules, errors, catalog_version)."""
    errors: list[str] = []
    if not path.exists():
        return [], [f"catalog file not found: {path}"], 0
    try:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
    except Exception as e:
        return [], [f"catalog load failed: {e}"], 0
    if not isinstance(data, dict):
        return [], ["catalog root must be a YAML mapping"], 0
    version = int(data.get("catalog_version") or 0)
    raw_rules = data.get("rules") or []
    if not isinstance(raw_rules, list):
        return [], ["`rules:` must be a list"], version
    rules: list[dict[str, Any]] = []
    for idx, r in enumerate(raw_rules):
        if not isinstance(r, dict):
            errors.append(f"rule #{idx} is not a mapping; skipped")
            continue
        rules.append({
            "id":          r.get("id"),
            "severity":    r.get("severity"),
            "pattern":     r.get("pattern"),
            "applies_to":  list(r.get("applies_to") or []),
            "rationale":   r.get("rationale") or "",
            "references":  list(r.get("references") or []),
        })
    return rules, errors, version


def _read_security_events(
    chain_path: Path, *, limit: int = 100,
) -> list[dict[str, Any]]:
    """Tail the JSONL chain reading ``agent_security_scan_completed``
    events. Returns newest-first up to ``limit``."""
    if not chain_path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        text = chain_path.read_text(encoding="utf-8")
    except Exception:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("event_type") == "agent_security_scan_completed":
            out.append(ev)
    out.reverse()
    return out[:limit]


def _walk_quarantined(staged_roots: tuple[Path, ...]) -> list[dict[str, Any]]:
    """For each staged-proposal root, list child dirs containing
    REJECTED.md. Each entry carries the dir path + REJECTED.md
    excerpt + mtime for the operator's view."""
    out: list[dict[str, Any]] = []
    for root in staged_roots:
        if not root.exists():
            continue
        try:
            children = sorted(root.iterdir())
        except Exception:
            continue
        for d in children:
            if not d.is_dir():
                continue
            marker = d / "REJECTED.md"
            if not marker.exists():
                continue
            try:
                excerpt = marker.read_text(encoding="utf-8")[:1500]
            except Exception:
                excerpt = ""
            try:
                mtime = datetime.fromtimestamp(
                    marker.stat().st_mtime, tz=timezone.utc,
                ).isoformat()
            except Exception:
                mtime = None
            out.append({
                "staged_dir":      str(d),
                "kind":            "skill" if "skills" in str(root) else "tool",
                "rejected_at":     mtime,
                "marker_excerpt":  excerpt,
            })
    return out


# ---- endpoints ------------------------------------------------------------


@router.get(
    "/status",
    dependencies=[Depends(require_api_token)],
)
def status_summary(
    request: Request,
    audit = Depends(get_audit_chain),
) -> dict[str, Any]:
    """Combined summary card for the pane's hero block."""
    rules, errors, version = _load_iocs(DEFAULT_IOC_CATALOG)

    chain_path = Path(audit.path) if hasattr(audit, "path") else None
    recent = _read_security_events(chain_path, limit=500) if chain_path else []
    one_day_ago = datetime.now(timezone.utc) - timedelta(days=1)

    def _in_last_day(ev_iso: str | None) -> bool:
        if not ev_iso:
            return False
        try:
            t = datetime.fromisoformat(ev_iso.replace("Z", "+00:00"))
        except Exception:
            return False
        return t >= one_day_ago

    refused_24h = sum(
        1 for ev in recent
        if ev.get("event_data", {}).get("decision") == "refuse"
        and _in_last_day(ev.get("timestamp"))
    )
    allowed_24h = sum(
        1 for ev in recent
        if ev.get("event_data", {}).get("decision") == "allow"
        and _in_last_day(ev.get("timestamp"))
    )
    critical_24h = sum(
        1 for ev in recent
        if ev.get("event_data", {}).get("critical_count", 0) > 0
        and _in_last_day(ev.get("timestamp"))
    )

    # Per-surface breakdown so the operator can see WHERE
    # refusals are happening (marketplace install vs forge
    # stage vs etc.). Useful for "is our LLM hallucinating
    # malicious code or are we just installing bad plugins?"
    surface_counts: dict[str, int] = {}
    for ev in recent:
        kind = ev.get("event_data", {}).get("install_kind", "unknown")
        surface_counts[kind] = surface_counts.get(kind, 0) + 1

    quarantined = _walk_quarantined(DEFAULT_STAGED_ROOTS)

    return {
        "ioc_rule_count":       len(rules),
        "ioc_catalog_version":  version,
        "ioc_catalog_errors":   errors,
        "ioc_catalog_path":     str(DEFAULT_IOC_CATALOG),
        "refused_last_24h":     refused_24h,
        "allowed_last_24h":     allowed_24h,
        "critical_last_24h":    critical_24h,
        "quarantined_count":    len(quarantined),
        "surface_counts":       surface_counts,
        "adr_tranches_shipped": ["T1", "T2", "T3", "T4", "T5", "T6"],
    }


@router.get(
    "/iocs",
    dependencies=[Depends(require_api_token)],
)
def list_iocs() -> dict[str, Any]:
    """Return the loaded IoC catalog."""
    rules, errors, version = _load_iocs(DEFAULT_IOC_CATALOG)
    # Sort by severity rank (CRITICAL first) then by id so the
    # operator's eye lands on the worst entries first.
    severity_rank = {
        "CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0,
    }
    rules.sort(
        key=lambda r: (
            -severity_rank.get(r.get("severity") or "", -1),
            r.get("id") or "",
        ),
    )
    return {
        "rules":            rules,
        "rule_count":       len(rules),
        "catalog_version":  version,
        "catalog_errors":   errors,
        "catalog_path":     str(DEFAULT_IOC_CATALOG),
    }


@router.get(
    "/recent-scans",
    dependencies=[Depends(require_api_token)],
)
def recent_scans(
    limit: int = Query(100, ge=1, le=1000),
    audit = Depends(get_audit_chain),
) -> dict[str, Any]:
    """Last N agent_security_scan_completed events, newest first."""
    chain_path = Path(audit.path) if hasattr(audit, "path") else None
    if chain_path is None:
        return {"events": [], "count": 0}
    events = _read_security_events(chain_path, limit=limit)
    return {"events": events, "count": len(events)}


@router.get(
    "/quarantined",
    dependencies=[Depends(require_api_token)],
)
def list_quarantined() -> dict[str, Any]:
    """List staged dirs with REJECTED.md present.

    Each entry includes the REJECTED.md excerpt so the operator
    can see WHY each was quarantined without opening individual
    files. The operator deletes REJECTED.md to override (which
    the install endpoints will still refuse via the structural
    check unless the file is genuinely gone).
    """
    quarantined = _walk_quarantined(DEFAULT_STAGED_ROOTS)
    return {"quarantined": quarantined, "count": len(quarantined)}


@router.post(
    "/reload",
    dependencies=[Depends(require_api_token)],
)
def reload_catalog() -> dict[str, Any]:
    """Hot-reload the IoC catalog.

    The security_scan.v1 tool reads the catalog fresh on every
    invocation today (no module-level cache), so this is
    effectively a no-op that returns the post-reload state for
    the frontend's convenience. A future cache layer makes
    this endpoint cache-invalidating.
    """
    rules, errors, version = _load_iocs(DEFAULT_IOC_CATALOG)
    return {
        "ok":              True,
        "rule_count":      len(rules),
        "catalog_version": version,
        "catalog_errors":  errors,
        "catalog_path":    str(DEFAULT_IOC_CATALOG),
    }
