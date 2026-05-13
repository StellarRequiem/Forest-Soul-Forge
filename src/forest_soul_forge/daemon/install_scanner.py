"""ADR-0062 T4 install-time scanner gate.

Thin shared helper consumed by the three install endpoints
(``/marketplace/install``, ``/skills/install``, ``/tools/install``).
Each install path resolves a *staging directory* containing the
candidate artifact; this module runs ``security_scan.v1`` over
that directory and decides whether to refuse the install based
on the catalog's severity findings.

## Policy

- **CRITICAL** finding → ALWAYS refuse. These are direct matches
  to active 2025-26 attacks (MCP STDIO RCE, home-dir wipe,
  eval(atob(...)) obfuscation, etc.). No legitimate plugin
  should trip these patterns.
- **HIGH** finding + ``strict=True`` → refuse. HIGH covers
  credential-harvest + short-lived-domain beacons + env-var
  exfil — likely-malicious but with potential false-positive
  surface. Operators opt into strict mode via ``?strict=true``
  on the install endpoint.
- **HIGH** finding + ``strict=False`` → allow but tag the
  response with a structured warning array so the operator sees
  the signal post-install.
- **MEDIUM / LOW / INFO** → allow; surfaced as informational
  fields in the response.

## Audit

Every gate decision emits ``agent_security_scan_completed`` to
the audit chain with: scan_kind ("install_gate"), staging_dir,
finding counts by severity, decision ("allow" | "refuse"), and
the scan_fingerprint. Operators auditing "what got refused
yesterday?" filter the chain on this event type.

## API surface

The single entry point is :func:`scan_install_or_refuse`.
Endpoints call it inline; on REFUSE it raises an
:class:`InstallGateRefused` exception with the structured
finding payload. The endpoint catches it and converts to a
409 with the same payload.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.tools.base import ToolContext
from forest_soul_forge.tools.builtin.security_scan import SecurityScanTool


# ---- exception types -----------------------------------------------------


class InstallGateRefused(Exception):
    """Raised when the install-time scanner finds a blocking
    issue. Carries a structured payload so the endpoint can
    serialize it to a 409 body directly.

    Attributes:
      severity_tier: the highest-severity finding the gate
        refused on ("CRITICAL" or "HIGH").
      strict: whether the refusal was triggered in strict mode.
      payload: the full {findings, by_severity, scan_fingerprint,
        staging_dir, decision} dict suitable for HTTP detail.
    """

    def __init__(self, severity_tier: str, strict: bool, payload: dict[str, Any]):
        super().__init__(
            f"install refused — {severity_tier} security finding(s) "
            f"(strict={strict})"
        )
        self.severity_tier = severity_tier
        self.strict = strict
        self.payload = payload


# ---- the gate ------------------------------------------------------------


def scan_install_or_refuse(
    *,
    staging_dir: Path,
    install_kind: str,
    strict: bool,
    audit_chain: AuditChain,
    operator_label: str | None = None,
    agent_dna: str | None = None,
    catalog_path: Path | None = None,
) -> dict[str, Any]:
    """Run security_scan over the staging dir; refuse on CRITICAL
    (or HIGH if strict). Emits the audit event in both
    allow + refuse paths.

    Returns the scan summary dict on ALLOW. Raises
    :class:`InstallGateRefused` on REFUSE.

    ``install_kind`` is a free-form label for the audit event
    (e.g. ``"marketplace"``, ``"skill_forge"``, ``"tool_forge"``).

    ``catalog_path`` is optional — defaults to the SecurityScanTool's
    config/security_iocs.yaml path. Tests pass a synthetic catalog
    here to exercise refuse/allow paths deterministically.
    """
    tool = SecurityScanTool()
    # We don't need a real ToolContext for the scan — security_scan
    # is purely path-driven. A minimal ctx satisfies the dataclass.
    ctx = ToolContext(
        instance_id="install_gate",
        agent_dna=agent_dna or "install-gate",
        role="security_low",
        genre="security_low",
        session_id=None,
    )
    args: dict[str, Any] = {
        "scan_kind":  "all",
        "scan_paths": [str(staging_dir)],
    }
    if catalog_path is not None:
        args["catalog_path"] = str(catalog_path)

    # The tool is async; run it inline. We can't `await` because
    # the caller may be a sync handler.
    try:
        result = asyncio.run(tool.execute(args, ctx))
    except RuntimeError:
        # Already inside an event loop (FastAPI async handler).
        # Use a thread-isolated loop to drive the coroutine.
        import concurrent.futures

        def _run_in_thread() -> Any:
            return asyncio.run(tool.execute(args, ctx))

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            result = ex.submit(_run_in_thread).result()

    findings: list[dict] = list(result.output["findings"])
    by_severity: dict[str, int] = dict(result.output["by_severity"])
    scan_fingerprint: str = result.output["scan_fingerprint"]

    critical_findings = [f for f in findings if f["severity"] == "CRITICAL"]
    high_findings = [f for f in findings if f["severity"] == "HIGH"]

    decision = "allow"
    refused_tier: str | None = None
    if critical_findings:
        decision = "refuse"
        refused_tier = "CRITICAL"
    elif strict and high_findings:
        decision = "refuse"
        refused_tier = "HIGH"

    payload = {
        "scan_kind":           "install_gate",
        "install_kind":        install_kind,
        "staging_dir":         str(staging_dir),
        "decision":            decision,
        "refused_on_tier":     refused_tier,
        "strict":              strict,
        "by_severity":         by_severity,
        "findings":            findings,
        "scan_fingerprint":    scan_fingerprint,
        "catalog_rule_count":  result.output["catalog_rule_count"],
        "catalog_errors":      result.output["catalog_errors"],
    }

    # Audit event: always emit, regardless of allow/refuse.
    # Operators auditing "what did we refuse?" filter on
    # decision=="refuse"; "what's the false-positive rate look
    # like in production?" → look at allows with non-empty
    # high/medium finding counts.
    try:
        audit_chain.append(
            "agent_security_scan_completed",
            {
                "install_kind":      install_kind,
                "staging_dir":       str(staging_dir),
                "decision":          decision,
                "refused_on_tier":   refused_tier,
                "strict":            strict,
                "operator_label":    operator_label,
                # event_data carries COUNTS, not the full findings list
                # — keeps the chain entry size bounded. The full list
                # lives in the HTTP response body + the daemon logs.
                "critical_count":    by_severity.get("CRITICAL", 0),
                "high_count":        by_severity.get("HIGH", 0),
                "medium_count":      by_severity.get("MEDIUM", 0),
                "low_count":         by_severity.get("LOW", 0),
                "info_count":        by_severity.get("INFO", 0),
                "scan_fingerprint":  scan_fingerprint,
            },
        )
    except Exception:
        # Audit-emit failure must NOT mask the gate's actual
        # decision. The gate is a security control; degrading
        # auditability is preferable to letting a CRITICAL
        # install slip through because the chain is broken.
        pass

    if decision == "refuse":
        raise InstallGateRefused(refused_tier or "UNKNOWN", strict, payload)

    return payload
