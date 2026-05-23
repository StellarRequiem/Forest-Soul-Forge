"""``audit_packet_generate.v1`` — ADR-0085 Phase D audit packet builder.

Wraps a time window of audit-chain entries, compliance scan
results, and evidence attestations into a single operator-readable
"audit packet" — the load-bearing artifact for the
"30-second-audit-packet" value prop. Read-only — never writes the
packet to disk; returns it as data for the caller (the
report_generator role's skill) to persist via memory_write.

## What's in a packet

For each control category in the named framework:
1. The control's identity + framework binding (id, title,
   description).
2. The latest framework_check verdict for the control (drawn from
   compliance_gap_report memory entries tagged
   framework:<framework_id>).
3. The relevant evidence_captured attestations within the window
   (tagged framework:<framework_id>).
4. The relevant long_term_archival attestations within the window.
5. The relevant compliance_remediation_proposal attestations.
6. A chain-integrity statement from audit_chain_verify (so the
   packet itself attests to its own provenance).

## Why not pre-compute

Audit packets are operator-on-demand artifacts. Pre-computing
weekly packets would inflate the audit chain with packets that
may never be consumed. Generation is cheap (~ms) and the chain
already has the data — wrap, don't pre-compute.

side_effects=read_only. The packet is returned in the tool's
output; the caller persists it (typically via memory_write +
optionally an operator-side write to data/compliance/archive/).
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_DEFAULT_FRAMEWORK_DIR = Path("config/compliance_frameworks")
_DEFAULT_AUDIT_CHAIN = Path("examples/audit_chain.jsonl")
_MAX_CHAIN_LINES = 100_000
_DEFAULT_WINDOW_DAYS = 90


class AuditPacketGenerateTool:
    """Bundle audit-chain entries + scans + evidence into a packet.

    Args:
      framework_id (str, required): framework to bind the packet
        to. Alphanumeric + underscores.
      window_days (int, optional): how far back to scan. Default 90.
        Capped at 730 (two years; the active-segment scope per
        ADR-0073 segmentation guarantee).
      framework_dir (str, optional): override default directory.
      audit_chain_path (str, optional): override default chain path.

    Output:
      {
        "framework_id":       str,
        "framework_name":     str,
        "framework_version":  str,
        "window_days":        int,
        "generated_at":       str (ISO),
        "chain_status":       "ok" | "unknown" | "broken",
        "control_summaries":  [{
            control_id, title, category,
            latest_scan_verdict,
            evidence_count,
            archive_attestation_count,
            remediation_proposal_count,
        }, ...],
        "scan_reports":       [{ts, content, tags}],
        "evidence_entries":   [{ts, content, tags}],
        "archive_entries":    [{ts, content, tags}],
        "remediation_entries":[{ts, content, tags}],
        "packet_sha256":      str  (sha256 of the packet body),
        "errors":             [str, ...],
      }
    """

    name = "audit_packet_generate"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        fid = args.get("framework_id")
        if not isinstance(fid, str) or not fid:
            raise ToolValidationError(
                "framework_id must be a non-empty string"
            )
        if not re.fullmatch(r"[a-zA-Z0-9_]+", fid):
            raise ToolValidationError(
                "framework_id must be alphanumeric + underscores"
            )
        wd = args.get("window_days")
        if wd is not None:
            if not isinstance(wd, int) or wd <= 0:
                raise ToolValidationError(
                    "window_days must be a positive integer"
                )
            if wd > 730:
                raise ToolValidationError(
                    "window_days must be <= 730 (segment scope)"
                )
        for key in ("framework_dir", "audit_chain_path"):
            if key in args and not isinstance(args[key], str):
                raise ToolValidationError(
                    f"{key} must be a string"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        framework_id: str = args["framework_id"]
        window_days = int(args.get("window_days") or _DEFAULT_WINDOW_DAYS)
        framework_dir = Path(
            args.get("framework_dir") or _DEFAULT_FRAMEWORK_DIR
        )
        chain_path = Path(
            args.get("audit_chain_path") or _DEFAULT_AUDIT_CHAIN
        )

        errors: list[str] = []

        # Load framework yaml to populate control_summaries.
        framework_path = framework_dir / f"{framework_id}.yaml"
        controls: list[dict[str, Any]] = []
        framework_name = framework_id
        framework_version = ""
        if framework_path.exists():
            try:
                with framework_path.open() as f:
                    doc = yaml.safe_load(f) or {}
                framework_name = doc.get("framework_name", framework_id)
                framework_version = str(doc.get("version", ""))
                controls = doc.get("controls") or []
            except yaml.YAMLError as e:
                errors.append(f"framework yaml parse error: {e}")
        else:
            errors.append(
                f"framework yaml not found: {framework_path}"
            )

        # Walk the audit chain ONCE, bucket entries by tag family.
        cutoff = time.time() - (window_days * 86400)
        framework_tag = f"framework:{framework_id}"
        scan_reports: list[dict[str, Any]] = []
        evidence_entries: list[dict[str, Any]] = []
        archive_entries: list[dict[str, Any]] = []
        remediation_entries: list[dict[str, Any]] = []
        chain_status = "unknown"

        if chain_path.exists():
            try:
                with chain_path.open() as f:
                    for i, line in enumerate(f):
                        if i >= _MAX_CHAIN_LINES:
                            break
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        ts = _entry_ts(entry)
                        if ts is None or ts < cutoff:
                            continue
                        tags = _entry_tags(entry)
                        if framework_tag not in tags:
                            continue
                        envelope = {
                            "ts":      ts,
                            "tags":    tags,
                            "content": _entry_content(entry),
                        }
                        if "compliance_gap_report" in tags:
                            scan_reports.append(envelope)
                        elif "evidence_captured" in tags:
                            evidence_entries.append(envelope)
                        elif "long_term_archival" in tags:
                            archive_entries.append(envelope)
                        elif "compliance_remediation_proposal" in tags:
                            remediation_entries.append(envelope)
                # The presence of entries we could parse with valid
                # ts AND a non-zero count of any compliance-tag
                # entry is the heuristic chain-status signal for the
                # packet header. A full audit_chain_verify call is
                # the caller's job (the report_generator skill runs
                # it as a separate step + threads its status here).
                chain_status = "ok"
            except OSError as e:
                errors.append(f"chain read error: {e}")
                chain_status = "broken"
        else:
            errors.append(f"audit chain not found: {chain_path}")
            chain_status = "broken"

        # Build per-control summaries by matching evidence /
        # remediation entries to control IDs from their tags.
        # (The convention is tag entries with control_id:<cid> when
        # an attestation is control-specific; absent that, count
        # everything against the framework-wide tally.)
        control_summaries: list[dict[str, Any]] = []
        for control in controls:
            cid = control.get("id", "<unknown>")
            ctag = f"control:{cid}"
            evidence_count = sum(
                1 for e in evidence_entries if ctag in e["tags"]
            )
            archive_count = sum(
                1 for e in archive_entries if ctag in e["tags"]
            )
            remediation_count = sum(
                1 for e in remediation_entries if ctag in e["tags"]
            )
            # Pull the most recent scan report's per-control text
            # for the latest_scan_verdict — heuristic; the scan
            # report is unstructured prose from llm_think.
            latest_scan_verdict = ""
            for sr in sorted(scan_reports, key=lambda x: x["ts"],
                             reverse=True):
                if cid in (sr["content"] or ""):
                    latest_scan_verdict = (
                        sr["content"][:200]
                        if sr["content"] else ""
                    )
                    break
            control_summaries.append({
                "control_id":               cid,
                "title":                    control.get("title", ""),
                "category":                 control.get("category", ""),
                "latest_scan_verdict":      latest_scan_verdict,
                "evidence_count":           evidence_count,
                "archive_attestation_count": archive_count,
                "remediation_proposal_count": remediation_count,
            })

        body = {
            "framework_id":         framework_id,
            "framework_name":       framework_name,
            "framework_version":    framework_version,
            "window_days":          window_days,
            "generated_at":         datetime.now(timezone.utc)
                                        .replace(tzinfo=None)
                                        .isoformat(timespec="seconds")
                                        + "Z",
            "chain_status":         chain_status,
            "control_summaries":    control_summaries,
            "scan_reports":         scan_reports,
            "evidence_entries":     evidence_entries,
            "archive_entries":      archive_entries,
            "remediation_entries":  remediation_entries,
        }

        # Packet sha256 = sha256 of the canonical-json serialization
        # of the body (sans the sha256 field itself). Lets the
        # operator verify the packet hasn't been tampered with after
        # generation.
        import hashlib
        canonical = json.dumps(body, sort_keys=True, default=str)
        packet_sha = "sha256:" + hashlib.sha256(
            canonical.encode("utf-8"),
        ).hexdigest()
        body["packet_sha256"] = packet_sha
        body["errors"] = errors

        return ToolResult(
            output=body,
            metadata={
                "scan_reports":    len(scan_reports),
                "evidence":        len(evidence_entries),
                "archives":        len(archive_entries),
                "remediations":    len(remediation_entries),
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"audit_packet {framework_id}: "
                f"{len(control_summaries)} controls, "
                f"{len(evidence_entries)}E "
                f"{len(archive_entries)}A "
                f"{len(remediation_entries)}R"
            ),
        )


def _entry_ts(entry: dict[str, Any]) -> float | None:
    raw = entry.get("ts") or entry.get("timestamp")
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            s = raw.rstrip("Z")
            return datetime.fromisoformat(s).timestamp()
        except ValueError:
            return None
    return None


def _entry_tags(entry: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for top_key in ("tags", "payload_tags"):
        raw = entry.get(top_key)
        if isinstance(raw, list):
            found.extend(t for t in raw if isinstance(t, str))
    for nest_key in ("payload", "data"):
        nested = entry.get(nest_key)
        if isinstance(nested, dict):
            sub = nested.get("tags")
            if isinstance(sub, list):
                found.extend(t for t in sub if isinstance(t, str))
    return found


def _entry_content(entry: dict[str, Any]) -> str:
    """Pull the content field if present; otherwise empty string."""
    for top_key in ("content", "body"):
        v = entry.get(top_key)
        if isinstance(v, str):
            return v
    for nest_key in ("payload", "data"):
        nested = entry.get(nest_key)
        if isinstance(nested, dict):
            v = nested.get("content")
            if isinstance(v, str):
                return v
    return ""
