"""``policy_lint.v1`` — ADR-0085 Phase C compliance policy linter.

Reads operator-supplied configuration files + a compliance
framework's rule set, evaluates lint findings, and emits a
proposal for each violation. **Does not apply remediations.**
The policy_enforcer role consumes these proposals + escalates
to the operator for approval; remediation execution itself is
operator-driven shell/file work, not a tool side-effect.

## Why a distinct tool from framework_check.v1

framework_check.v1 (Phase B) evaluates *system-level* rules:
"does this file exist?", "is this tag in the chain?". It's a
verification tool — surface gaps.

policy_lint.v1 (Phase C) evaluates *config-content* rules: "does
this YAML have a forbidden setting?", "is a required key missing
from this config?". It's a content tool — propose remediations
keyed to specific files + keys.

The two tools share the framework_id loader infrastructure but
operate on different rule surfaces. policy_lint expects a
*separate* rules section in the framework YAML under
``lint_rules`` — when absent, the linter reports an empty
finding set rather than treating the absence as an error.

## Rule kinds (policy_lint-specific)

- ``yaml_key_required``     — the named key must be present (and
                              optionally have value matching
                              params.expected_value or matching
                              params.value_pattern).
- ``yaml_key_forbidden``    — the named key must NOT be present
                              in the YAML.
- ``file_max_age_days``     — the file's mtime must be at most N
                              days old (rotation discipline).

Each rule produces a ``finding`` with ``proposal`` text that the
operator reviews before applying.

side_effects=read_only — the linter reads files + the framework
yaml; the proposed remediations are returned as data, never
written to disk by this tool.
"""
from __future__ import annotations

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
_MAX_TARGET_FILES = 100
_MAX_FILE_BYTES = 4 * 1024 * 1024


class PolicyLintTool:
    """Lint operator configs against a framework's lint rules.

    Args:
      framework_id (str, required): slug matching a YAML under
        ``config/compliance_frameworks/``. Same alphanumeric +
        underscores constraint as framework_check.v1.
      target_paths (list[str], required): files to lint.
        Capped at 100 files / 4 MiB per file per call.
      framework_dir (str, optional): override default framework
        directory. Useful for tests.
      rule_ids (list[str], optional): if present, only evaluate
        these rule_ids from the framework's lint_rules.

    Output:
      {
        "framework_id":    str,
        "framework_name":  str,
        "files_evaluated": int,
        "findings":        [{
          file, rule_id, severity, kind, message, proposal,
        }, ...],
        "errors":          [str, ...],
      }
    """

    name = "policy_lint"
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
        tp = args.get("target_paths")
        if not isinstance(tp, list) or not tp:
            raise ToolValidationError(
                "target_paths must be a non-empty list of strings"
            )
        if len(tp) > _MAX_TARGET_FILES:
            raise ToolValidationError(
                f"target_paths capped at {_MAX_TARGET_FILES}; "
                f"got {len(tp)}"
            )
        if not all(isinstance(p, str) and p for p in tp):
            raise ToolValidationError(
                "target_paths entries must be non-empty strings"
            )
        if "framework_dir" in args and not isinstance(
            args["framework_dir"], str,
        ):
            raise ToolValidationError("framework_dir must be a string")
        rids = args.get("rule_ids")
        if rids is not None:
            if not isinstance(rids, list):
                raise ToolValidationError(
                    "rule_ids must be a list of strings"
                )
            if not all(isinstance(r, str) and r for r in rids):
                raise ToolValidationError(
                    "rule_ids entries must be non-empty strings"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        framework_id: str = args["framework_id"]
        target_paths: list[str] = args["target_paths"]
        framework_dir = Path(
            args.get("framework_dir") or _DEFAULT_FRAMEWORK_DIR
        )
        rule_filter = (
            set(args["rule_ids"]) if args.get("rule_ids") else None
        )

        errors: list[str] = []
        framework_path = framework_dir / f"{framework_id}.yaml"
        if not framework_path.exists():
            return _empty_output(
                framework_id,
                errors=[f"framework yaml not found: {framework_path}"],
            )

        try:
            with framework_path.open() as f:
                doc = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            return _empty_output(
                framework_id,
                errors=[f"yaml parse error: {e}"],
            )

        framework_name = doc.get("framework_name", framework_id)
        lint_rules = doc.get("lint_rules") or []
        if rule_filter:
            lint_rules = [
                r for r in lint_rules
                if isinstance(r, dict)
                and r.get("rule_id") in rule_filter
            ]

        findings: list[dict[str, Any]] = []
        files_evaluated = 0
        for raw in target_paths:
            expanded = os.path.expanduser(raw)
            path = Path(expanded)
            if not path.exists():
                errors.append(f"not_found:{raw}")
                continue
            try:
                if path.stat().st_size > _MAX_FILE_BYTES:
                    errors.append(f"too_large:{raw}")
                    continue
            except OSError as e:
                errors.append(f"stat_error:{raw}:{e}")
                continue

            files_evaluated += 1
            for rule in lint_rules:
                if not isinstance(rule, dict):
                    continue
                for finding in _evaluate_rule_against_file(
                    rule, path,
                ):
                    findings.append({
                        "file":     str(path),
                        "rule_id":  rule.get("rule_id", "<unknown>"),
                        "severity": rule.get("severity", "low"),
                        "kind":     rule.get("kind", "<unknown>"),
                        "message":  finding["message"],
                        "proposal": finding["proposal"],
                    })

        return ToolResult(
            output={
                "framework_id":    framework_id,
                "framework_name":  framework_name,
                "files_evaluated": files_evaluated,
                "findings":        findings,
                "errors":          errors,
            },
            metadata={
                "framework_path": str(framework_path),
                "rules_loaded":   len(lint_rules),
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"policy_lint {framework_id}: "
                f"{len(findings)} findings in {files_evaluated} files"
            ),
        )


def _empty_output(framework_id: str, errors: list[str]) -> ToolResult:
    return ToolResult(
        output={
            "framework_id":    framework_id,
            "framework_name":  "",
            "files_evaluated": 0,
            "findings":        [],
            "errors":          errors,
        },
        metadata={},
        tokens_used=None, cost_usd=None,
        side_effect_summary=f"policy_lint {framework_id}: error",
    )


def _evaluate_rule_against_file(
    rule: dict[str, Any],
    path: Path,
) -> list[dict[str, str]]:
    """Return zero-or-more findings for one rule on one file.

    Each finding is a {"message", "proposal"} dict.
    """
    kind = rule.get("kind", "")
    params = rule.get("params") or {}
    file_pattern = params.get("file_pattern")
    # Optional gating: rule only applies to files matching this regex
    # on their path. Lets the framework yaml scope rules tightly.
    if isinstance(file_pattern, str) and file_pattern:
        if not re.search(file_pattern, str(path)):
            return []

    if kind == "yaml_key_required":
        return _check_yaml_key_required(params, path, rule)
    if kind == "yaml_key_forbidden":
        return _check_yaml_key_forbidden(params, path, rule)
    if kind == "file_max_age_days":
        return _check_file_max_age(params, path, rule)
    return []


def _read_yaml(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return None
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(doc, dict):
        return None
    return doc


def _lookup_key(doc: dict[str, Any], dotted: str) -> tuple[bool, Any]:
    """Walk a dotted-path lookup. Returns (found, value)."""
    cur: Any = doc
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return (False, None)
    return (True, cur)


def _check_yaml_key_required(
    params: dict[str, Any],
    path: Path,
    rule: dict[str, Any],
) -> list[dict[str, str]]:
    key = params.get("key")
    if not isinstance(key, str) or not key:
        return []
    doc = _read_yaml(path)
    if doc is None:
        return [{
            "message":  f"file {path} is not parseable yaml",
            "proposal": (
                "Verify the yaml is well-formed. "
                "Linter cannot evaluate rule."
            ),
        }]
    found, value = _lookup_key(doc, key)
    if not found:
        return [{
            "message": f"required key '{key}' missing from {path}",
            "proposal": (
                f"Add '{key}' to {path}. "
                f"Severity: {rule.get('severity', 'low')}. "
                f"Hint: {rule.get('remediation', '').strip()}"
            ),
        }]
    expected = params.get("expected_value")
    if expected is not None and value != expected:
        return [{
            "message":  (
                f"key '{key}' in {path} is {value!r}; "
                f"expected {expected!r}"
            ),
            "proposal": (
                f"Set '{key}' = {expected!r} in {path}. "
                f"Hint: {rule.get('remediation', '').strip()}"
            ),
        }]
    pattern = params.get("value_pattern")
    if isinstance(pattern, str) and pattern:
        try:
            if not re.search(pattern, str(value)):
                return [{
                    "message":  (
                        f"key '{key}' in {path} is {value!r}; "
                        f"does not match /{pattern}/"
                    ),
                    "proposal": (
                        f"Update '{key}' in {path} to match "
                        f"/{pattern}/. "
                        f"Hint: {rule.get('remediation', '').strip()}"
                    ),
                }]
        except re.error:
            return [{
                "message":  f"rule has invalid regex /{pattern}/",
                "proposal": (
                    "Fix the rule's value_pattern in the "
                    "framework yaml; linter cannot evaluate."
                ),
            }]
    return []


def _check_yaml_key_forbidden(
    params: dict[str, Any],
    path: Path,
    rule: dict[str, Any],
) -> list[dict[str, str]]:
    key = params.get("key")
    if not isinstance(key, str) or not key:
        return []
    doc = _read_yaml(path)
    if doc is None:
        return []
    found, _ = _lookup_key(doc, key)
    if found:
        return [{
            "message":  f"forbidden key '{key}' present in {path}",
            "proposal": (
                f"Remove '{key}' from {path}. "
                f"Hint: {rule.get('remediation', '').strip()}"
            ),
        }]
    return []


def _check_file_max_age(
    params: dict[str, Any],
    path: Path,
    rule: dict[str, Any],
) -> list[dict[str, str]]:
    max_days = params.get("max_days")
    if not isinstance(max_days, (int, float)) or max_days <= 0:
        return []
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    age_seconds = time.time() - mtime
    cap = max_days * 86400
    if age_seconds > cap:
        age_days = age_seconds / 86400
        return [{
            "message":  (
                f"{path} mtime is {age_days:.1f} days old; "
                f"max is {max_days} days"
            ),
            "proposal": (
                f"Rotate or refresh {path}. "
                f"Hint: {rule.get('remediation', '').strip()}"
            ),
        }]
    return []
