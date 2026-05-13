"""ADR-0062 T5 forge-stage IoC scanner.

Companion to ``daemon/install_scanner.py``:
  - Install scanner (T4) catches malicious artifacts at INSTALL time,
    after the operator approves a proposal.
  - This module catches them at STAGE time, BEFORE the operator
    ever sees the LLM-generated output.

Why two layers: the install gate protects against operator
human-error (clicking install on a malicious staged proposal).
This gate protects against the LLM itself emitting hallucinated
hostile code — an attack vector that exists even when the
forge prompt is benign, because a poisoned model or a
prompt-injection through the description text can produce
malicious staged output.

## Policy (ADR-0062 D1 — same as T4)

- CRITICAL contradiction → REFUSE the propose. Write a
  ``REJECTED.md`` next to the staged artifacts so the
  rejection is permanently traceable, then raise
  :class:`ForgeStageRefused` for the endpoint to convert
  to a 409 with structured findings.
- HIGH / MEDIUM / LOW → ALLOW + return scan_summary in
  the response so the operator sees the signal.

## Audit

Every gate decision emits ``agent_security_scan_completed``
(same event type as the install gate, just with
install_kind='forge_skill_stage' or 'forge_tool_stage').
Lets an operator query the chain for all four scan
surfaces (marketplace install / skill install / tool install
/ forge stage) without parsing event_data shape.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.daemon.install_scanner import (
    InstallGateRefused,
    scan_install_or_refuse,
)


# ---- exception type -----------------------------------------------------


class ForgeStageRefused(Exception):
    """Raised when the forge stage scanner finds a CRITICAL issue.

    Attributes:
      severity_tier: "CRITICAL" (only tier that refuses at this gate).
      payload: structured scan summary suitable for HTTP detail.
      staged_dir: the dir we wrote REJECTED.md into.
    """

    def __init__(self, severity_tier: str, payload: dict[str, Any], staged_dir: Path):
        super().__init__(
            f"forge stage refused — {severity_tier} security finding(s) "
            f"in {staged_dir}"
        )
        self.severity_tier = severity_tier
        self.payload = payload
        self.staged_dir = staged_dir


# ---- the gate ---------------------------------------------------------


def scan_forge_stage_or_refuse(
    *,
    staged_dir: Path,
    forge_kind: str,            # "forge_skill_stage" | "forge_tool_stage"
    audit_chain: AuditChain,
    operator_label: str | None = None,
    agent_dna: str | None = None,
    catalog_path: Path | None = None,
) -> dict[str, Any]:
    """Scan a freshly-staged forge artifact + refuse on CRITICAL.

    Reuses :func:`scan_install_or_refuse` under the hood — same
    severity policy, same audit event, same SecurityScanTool
    catalog. The only differences:

      1. ``install_kind`` is set to the forge_kind so audit
         queries can separate the surfaces.
      2. ``strict=False`` is fixed — the forge stage gate's
         policy is "refuse only on CRITICAL, warn otherwise."
         (Operator can still pass `--strict` to the eventual
         install endpoint when they go to install the staged
         proposal; that's a separate gate.)
      3. On REFUSE we write ``REJECTED.md`` to the staged dir
         before re-raising as :class:`ForgeStageRefused` so the
         endpoint can return 409.

    Returns the scan summary dict on ALLOW. Raises on REFUSE.
    """
    try:
        result = scan_install_or_refuse(
            staging_dir=staged_dir,
            install_kind=forge_kind,
            strict=False,
            audit_chain=audit_chain,
            operator_label=operator_label,
            agent_dna=agent_dna,
            catalog_path=catalog_path,
        )
    except InstallGateRefused as e:
        # Write a permanent marker into the staged dir so the
        # rejection survives even if the operator never reloads
        # the page. The eventual install endpoint should refuse
        # to install any dir containing REJECTED.md.
        _write_rejection_marker(
            staged_dir=staged_dir,
            forge_kind=forge_kind,
            severity_tier=e.severity_tier,
            payload=e.payload,
        )
        raise ForgeStageRefused(
            severity_tier=e.severity_tier,
            payload=e.payload,
            staged_dir=staged_dir,
        ) from e
    return result


# ---- internals --------------------------------------------------------


def _write_rejection_marker(
    *,
    staged_dir: Path,
    forge_kind: str,
    severity_tier: str,
    payload: dict[str, Any],
) -> None:
    """Write ``REJECTED.md`` to the staged dir documenting the
    refusal. Best-effort — if the staged dir is somehow
    unwritable (it shouldn't be), the rejection still happens
    via the audit event + the raised exception.
    """
    if not staged_dir.exists():
        return
    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        findings = payload.get("findings") or []
        body_lines = [
            "# REJECTED — ADR-0062 forge-stage scanner refused this proposal",
            "",
            f"- timestamp: {ts}",
            f"- forge_kind: {forge_kind}",
            f"- severity_tier: {severity_tier}",
            f"- scan_fingerprint: {payload.get('scan_fingerprint', 'unknown')}",
            f"- catalog_rule_count: {payload.get('catalog_rule_count', 0)}",
            "",
            "## Why this proposal was refused",
            "",
            (
                "The LLM-generated artifact in this directory matched at "
                "least one CRITICAL IoC pattern from "
                "`config/security_iocs.yaml`. Per ADR-0062 T5, proposals "
                "with CRITICAL findings are quarantined: they are NOT "
                "returned to the operator as a successful propose, and "
                "the install endpoint will refuse to install this dir "
                "while REJECTED.md is present."
            ),
            "",
            "## Findings",
            "",
        ]
        for f in findings[:20]:  # cap to keep the file readable
            body_lines.append(
                f"- **{f.get('severity', '?')}** `{f.get('pattern_id', '?')}` "
                f"at `{f.get('file', '?')}:{f.get('line', '?')}`"
            )
            ev = f.get("evidence_excerpt") or ""
            if ev:
                body_lines.append(f"  - excerpt: `{ev[:200]}`")
            if f.get("rationale"):
                body_lines.append(f"  - rationale: {f['rationale']}")
        body_lines += [
            "",
            "## What to do",
            "",
            (
                "1. **Review the findings above** — confirm the LLM output "
                "is genuinely hostile or hallucinated; if it's a false "
                "positive, edit `config/security_iocs.yaml` to refine the "
                "rule."
            ),
            (
                "2. **Discard the staged proposal** — delete this directory "
                "(or leave it as a permanent audit artifact alongside the "
                "chain entry)."
            ),
            (
                "3. **Re-run the forge** with a refined description if you "
                "still want a similar capability."
            ),
            "",
            "## Removal",
            "",
            (
                "This file is the marker that prevents accidental install. "
                "Delete it explicitly only if you want to bypass the gate "
                "(operator override). The audit event lives in the chain "
                "regardless."
            ),
        ]
        (staged_dir / "REJECTED.md").write_text(
            "\n".join(body_lines), encoding="utf-8",
        )
    except Exception:
        # Best-effort. The refusal already lands via audit + raised
        # exception; the marker file is a convenience.
        pass


def staged_dir_is_quarantined(staged_dir: Path) -> bool:
    """True iff REJECTED.md is present in the staged dir.

    Install endpoints call this before promoting a staged
    proposal so a forge-stage refusal can't be silently bypassed
    by an operator clicking install on a quarantined dir. ADR-0062
    D1: REJECTED.md is the structural gate.
    """
    return (staged_dir / "REJECTED.md").exists()
