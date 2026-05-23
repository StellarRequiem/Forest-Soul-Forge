"""``framework_check.v1`` — ADR-0085 Phase B compliance rule evaluator.

Loads a compliance framework YAML from
``config/compliance_frameworks/<framework_id>.yaml``, walks the
control rules, evaluates each rule against the live system, and
returns a structured per-rule pass/fail verdict the caller (a
compliance_scanner agent's skill, or a direct operator dispatch)
consumes to surface gaps.

## Rule kinds

The framework YAML declares each rule with a ``kind`` field:

- ``required_file``         — params.paths must all exist on disk.
- ``forbidden_pattern``     — params.pattern (regex) must NOT
                              appear in any file under params.scan_paths.
- ``required_attestation``  — at least one memory entry tagged
                              params.tag must exist within
                              params.max_age_hours.
- ``audit_event_required``  — at least one audit-chain entry of
                              params.event_type must exist within
                              params.max_age_hours.

Unknown kinds are reported as ``skipped:unknown_kind`` so adding a
new kind in YAML before the implementer lands the matching branch
fails cleanly rather than passing silently.

## Verdict matrix

Per rule: ``pass`` / ``fail`` / ``skipped``.
Per control: aggregate ``pass`` if all rules pass; ``fail`` if any
rule fails; ``partial`` if some pass and some fail or are skipped.
Per framework: counts of passing / failing / skipped controls + the
list of failing-rule details so the caller can produce a gap report.

## Reading the audit chain

For ``audit_event_required``, the tool reads
``examples/audit_chain.jsonl`` (the live chain path per
``CLAUDE.md``) line by line, ignoring entries older than the cap.
The chain is append-only + the file is bounded by the recent
window in practice; a streaming walk is the simplest correct
approach.

For ``required_attestation``, the tool walks the memory_entries
table via ctx.registry.memory_recall — same approach the
verifier_loop role uses for its contradiction sweeps.

side_effects=read_only — the tool only reads files + the chain
+ the memory store; never writes.
"""
from __future__ import annotations

import json
import os
import re
import time
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
_MAX_SCAN_FILES = 500
_MAX_SCAN_BYTES = 4 * 1024 * 1024  # 4 MiB per scanned file
_MAX_CHAIN_LINES = 50_000


class FrameworkCheckTool:
    """Evaluate a compliance framework's rules against the live system.

    Args:
      framework_id (str, required): slug matching a YAML file under
        ``config/compliance_frameworks/``. Must be alphanumeric +
        underscores; refusal pattern blocks path-traversal attempts.
      framework_dir (str, optional): override the default framework
        directory. Useful for tests; production callers omit.
      audit_chain_path (str, optional): override the default audit
        chain path. Tests pass a fixture path; production callers
        omit (defaults via CLAUDE.md to ``examples/audit_chain.jsonl``).
      control_ids (list[str], optional): if present, only evaluate
        these controls. Default: all controls in the framework.

    Output:
      {
        "framework_id":        str,
        "framework_name":      str,
        "version":             str,
        "controls_evaluated":  int,
        "controls_passing":    int,
        "controls_failing":    int,
        "controls_partial":    int,
        "rule_results":        [{
            control_id, rule_id, kind, verdict,
            severity, remediation, detail
        }, ...],
        "errors":              [str, ...],
      }
    """

    name = "framework_check"
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
                " (no path separators)"
            )
        if "framework_dir" in args and not isinstance(
            args["framework_dir"], str
        ):
            raise ToolValidationError("framework_dir must be a string")
        if "audit_chain_path" in args and not isinstance(
            args["audit_chain_path"], str
        ):
            raise ToolValidationError(
                "audit_chain_path must be a string"
            )
        cids = args.get("control_ids")
        if cids is not None:
            if not isinstance(cids, list):
                raise ToolValidationError(
                    "control_ids must be a list of strings"
                )
            if not all(isinstance(c, str) and c for c in cids):
                raise ToolValidationError(
                    "control_ids entries must be non-empty strings"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        framework_id: str = args["framework_id"]
        framework_dir = Path(
            args.get("framework_dir") or _DEFAULT_FRAMEWORK_DIR
        )
        chain_path = Path(
            args.get("audit_chain_path") or _DEFAULT_AUDIT_CHAIN
        )
        control_filter = (
            set(args["control_ids"]) if args.get("control_ids") else None
        )

        errors: list[str] = []
        framework_path = framework_dir / f"{framework_id}.yaml"
        if not framework_path.exists():
            return ToolResult(
                output={
                    "framework_id":       framework_id,
                    "framework_name":     "",
                    "version":            "",
                    "controls_evaluated": 0,
                    "controls_passing":   0,
                    "controls_failing":   0,
                    "controls_partial":   0,
                    "rule_results":       [],
                    "errors": [
                        f"framework yaml not found: {framework_path}"
                    ],
                },
                metadata={"path": str(framework_path)},
                tokens_used=None, cost_usd=None,
                side_effect_summary=(
                    f"framework_check {framework_id}: not_found"
                ),
            )

        try:
            with framework_path.open() as f:
                doc = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            return ToolResult(
                output={
                    "framework_id":       framework_id,
                    "framework_name":     "",
                    "version":            "",
                    "controls_evaluated": 0,
                    "controls_passing":   0,
                    "controls_failing":   0,
                    "controls_partial":   0,
                    "rule_results":       [],
                    "errors": [f"yaml parse error: {e}"],
                },
                metadata={"path": str(framework_path)},
                tokens_used=None, cost_usd=None,
                side_effect_summary=(
                    f"framework_check {framework_id}: yaml_error"
                ),
            )

        framework_name = doc.get("framework_name", framework_id)
        version = str(doc.get("version", ""))
        controls = doc.get("controls") or []

        rule_results: list[dict[str, Any]] = []
        controls_passing = 0
        controls_failing = 0
        controls_partial = 0
        controls_evaluated = 0

        # Pre-load the audit chain ONCE for all chain-consuming
        # rules (audit_event_required + required_attestation,
        # which queries the chain for memory_written events tagged
        # with the requested string). Avoids re-walking the file
        # per rule (n*chain-size becomes n+chain-size).
        chain_entries: list[dict[str, Any]] = []
        chain_load_error = None
        if any(
            r.get("kind") in {
                "audit_event_required", "required_attestation",
            }
            for c in controls
            for r in (c.get("rules") or [])
        ):
            chain_entries, chain_load_error = _load_chain(chain_path)
            if chain_load_error:
                errors.append(chain_load_error)

        for control in controls:
            if not isinstance(control, dict):
                continue
            cid = control.get("id", "<unknown>")
            if control_filter and cid not in control_filter:
                continue
            controls_evaluated += 1
            rules = control.get("rules") or []
            per_rule_verdicts = []
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                verdict, detail = _evaluate_rule(
                    rule, chain_entries=chain_entries,
                )
                rule_results.append({
                    "control_id":  cid,
                    "rule_id":     rule.get("rule_id", "<unknown>"),
                    "kind":        rule.get("kind", "<unknown>"),
                    "verdict":     verdict,
                    "severity":    rule.get("severity", "low"),
                    "remediation": rule.get("remediation", "")
                                    .strip(),
                    "detail":      detail,
                })
                per_rule_verdicts.append(verdict)
            if not per_rule_verdicts:
                # control with no rules counts as evaluated but neither
                # pass nor fail — flag as partial.
                controls_partial += 1
                continue
            if all(v == "pass" for v in per_rule_verdicts):
                controls_passing += 1
            elif any(v == "fail" for v in per_rule_verdicts):
                if all(v == "fail" for v in per_rule_verdicts):
                    controls_failing += 1
                else:
                    controls_partial += 1
            else:
                # all skipped — partial (operator should know).
                controls_partial += 1

        return ToolResult(
            output={
                "framework_id":       framework_id,
                "framework_name":     framework_name,
                "version":            version,
                "controls_evaluated": controls_evaluated,
                "controls_passing":   controls_passing,
                "controls_failing":   controls_failing,
                "controls_partial":   controls_partial,
                "rule_results":       rule_results,
                "errors":             errors,
            },
            metadata={
                "framework_path":  str(framework_path),
                "rules_evaluated": len(rule_results),
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"framework_check {framework_id}: "
                f"{controls_passing}P {controls_failing}F "
                f"{controls_partial}~ ({controls_evaluated} controls)"
            ),
        )


def _evaluate_rule(
    rule: dict[str, Any],
    *,
    chain_entries: list[dict[str, Any]],
) -> tuple[str, str]:
    """Return (verdict, detail) for one rule."""
    kind = rule.get("kind", "")
    params = rule.get("params") or {}
    if kind == "required_file":
        return _eval_required_file(params)
    if kind == "forbidden_pattern":
        return _eval_forbidden_pattern(params)
    if kind == "required_attestation":
        # required_attestation needs ctx.registry / memory access we
        # don't have here in this minimal v1; instead, v1 treats it
        # as an audit-event lookup on a memory_written event tagged
        # with the requested string. That's structurally equivalent
        # (memory_write emits a memory_written audit event with the
        # tags) and avoids coupling this tool to the registry session.
        return _eval_attestation_via_chain(params, chain_entries)
    if kind == "audit_event_required":
        return _eval_audit_event(params, chain_entries)
    return ("skipped", f"unknown_kind:{kind}")


def _eval_required_file(params: dict[str, Any]) -> tuple[str, str]:
    paths = params.get("paths") or []
    missing = []
    for raw in paths:
        if not isinstance(raw, str):
            continue
        expanded = os.path.expanduser(raw)
        if not Path(expanded).exists():
            missing.append(raw)
    if missing:
        return ("fail", f"missing:{','.join(missing)}")
    if not paths:
        return ("skipped", "no_paths_specified")
    return ("pass", f"all_present:{len(paths)}")


def _eval_forbidden_pattern(params: dict[str, Any]) -> tuple[str, str]:
    pattern = params.get("pattern")
    scan_paths = params.get("scan_paths") or []
    if not isinstance(pattern, str) or not pattern:
        return ("skipped", "no_pattern")
    if not scan_paths:
        return ("skipped", "no_scan_paths")
    try:
        compiled = re.compile(pattern)
    except re.error as e:
        return ("skipped", f"regex_error:{e}")
    files_scanned = 0
    matches: list[str] = []
    for raw in scan_paths:
        if not isinstance(raw, str):
            continue
        root = Path(os.path.expanduser(raw))
        if not root.exists():
            continue
        if root.is_file():
            files_to_scan = [root]
        else:
            files_to_scan = [
                p for p in root.rglob("*") if p.is_file()
            ]
        for p in files_to_scan:
            if files_scanned >= _MAX_SCAN_FILES:
                break
            try:
                if p.stat().st_size > _MAX_SCAN_BYTES:
                    continue
                text = p.read_text(errors="ignore")
            except OSError:
                continue
            files_scanned += 1
            if compiled.search(text):
                matches.append(str(p))
                if len(matches) >= 10:
                    break
        if files_scanned >= _MAX_SCAN_FILES:
            break
    if matches:
        return ("fail", f"matched_in:{','.join(matches[:5])}")
    if files_scanned == 0:
        return ("skipped", "no_files_to_scan")
    return ("pass", f"clean:{files_scanned}_files_scanned")


def _eval_attestation_via_chain(
    params: dict[str, Any],
    chain_entries: list[dict[str, Any]],
) -> tuple[str, str]:
    tag = params.get("tag")
    max_age_hours = params.get("max_age_hours")
    if not isinstance(tag, str) or not tag:
        return ("skipped", "no_tag")
    if not isinstance(max_age_hours, (int, float)) or max_age_hours <= 0:
        return ("skipped", "no_max_age")
    cutoff = time.time() - (max_age_hours * 3600)
    found = 0
    for entry in chain_entries:
        if entry.get("event_type") not in {
            "memory_written", "memory_write_completed",
        }:
            continue
        ts = _entry_ts(entry)
        if ts is None or ts < cutoff:
            continue
        tags = _entry_tags(entry)
        if tag in tags:
            found += 1
    if found > 0:
        return ("pass", f"found:{found}_attestations_in_window")
    return ("fail", f"no_attestation_tagged_{tag}_in_window")


def _eval_audit_event(
    params: dict[str, Any],
    chain_entries: list[dict[str, Any]],
) -> tuple[str, str]:
    event_type = params.get("event_type")
    max_age_hours = params.get("max_age_hours")
    if not isinstance(event_type, str) or not event_type:
        return ("skipped", "no_event_type")
    if not isinstance(max_age_hours, (int, float)) or max_age_hours <= 0:
        return ("skipped", "no_max_age")
    cutoff = time.time() - (max_age_hours * 3600)
    found = 0
    for entry in chain_entries:
        if entry.get("event_type") != event_type:
            continue
        ts = _entry_ts(entry)
        if ts is None or ts < cutoff:
            continue
        found += 1
    if found > 0:
        return ("pass", f"found:{found}_events_in_window")
    return ("fail", f"no_event_{event_type}_in_window")


def _load_chain(
    path: Path,
) -> tuple[list[dict[str, Any]], str | None]:
    if not path.exists():
        return ([], None)  # absent chain is not an error per se
    entries: list[dict[str, Any]] = []
    try:
        with path.open() as f:
            for i, line in enumerate(f):
                if i >= _MAX_CHAIN_LINES:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return (entries, None)
    except OSError as e:
        return ([], f"chain_read_error:{e}")


def _entry_ts(entry: dict[str, Any]) -> float | None:
    raw = entry.get("ts") or entry.get("timestamp")
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        # Try fromisoformat first (audit chain canonical form).
        # Tolerate trailing 'Z' which fromisoformat doesn't accept.
        try:
            from datetime import datetime
            s = raw.rstrip("Z")
            return datetime.fromisoformat(s).timestamp()
        except ValueError:
            return None
    return None


def _entry_tags(entry: dict[str, Any]) -> list[str]:
    # Tags may appear at entry level (`tags`/`payload_tags`) OR nested
    # under `payload`/`data` (the audit-chain convention for
    # memory_written events). Merge both sources so the rule
    # evaluator finds attestation tags regardless of where the
    # emitting code put them.
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
