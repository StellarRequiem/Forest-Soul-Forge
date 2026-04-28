"""``continuous_verify.v1`` — periodic posture re-check + drift report.

ADR-0033 Phase B3. The high-tier 'never-trust-cached-state'
pattern: SecOpsSentinel calls this on a timer to re-run a list
of probes against a baseline (typically the prior call's output
written to memory at scope='lineage') and surface any drift.

This is a pure-Python composer — it does NOT shell out itself.
It accepts a ``current`` snapshot (usually built by calling
posture_check.v1 immediately before this) plus an optional
``baseline`` snapshot, and emits a drift report:

  * checks_added:    in current, not in baseline (new probe shipped)
  * checks_removed:  in baseline, not in current (probe disappeared)
  * checks_changed:  state or severity flipped
  * checks_steady:   identical between baseline and current

Severity escalation: any change from ok→warn promotes severity to
the warn-side severity; any state-change between non-ok states
keeps the higher of the two; baseline-vs-current severity
mismatches are flagged with ``severity_drift=True``. The overall
verdict is the max severity across all changed checks (or 'low'
if nothing drifted).

side_effects=read_only — composer only, no I/O. The skill that
wraps this calls posture_check, then memory_recall (for the
baseline), then continuous_verify, then memory_write to persist
the new snapshot for next round's diff.
"""
from __future__ import annotations

from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_SEVERITY_ORDER = ("low", "medium", "high", "critical")


class ContinuousVerifyTool:
    """Compute drift between a current and baseline posture snapshot.

    Args:
      current  (dict, required): a posture_check.v1 output (or any
        dict with a 'checks' list of {name, state, severity, value}).
      baseline (dict, optional): same shape as current. Empty / missing
        → every current check shows up in 'checks_added' and the
        verdict is the current snapshot's overall_severity.
      escalate_on_missing (bool, default false): when True, a check
        that vanished from current (in baseline but not current) is
        treated as severity='high' rather than 'medium'. Use when
        you trust the baseline more than the current run (e.g.
        baseline ran with elevated permissions, current didn't).

    Output:
      {
        "verdict":            "low"|"medium"|"high"|"critical",
        "checks_added":       [{"name":..., "state":..., "severity":...}, ...],
        "checks_removed":     [{"name":..., "severity":...}, ...],
        "checks_changed":     [{"name":..., "from": {...}, "to": {...}, "severity":...}, ...],
        "checks_steady":      [str, ...],            # names only
        "severity_drift":     bool,                  # any check changed severity?
        "summary":            str                    # short operator-facing line
      }
    """

    name = "continuous_verify"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        current = args.get("current")
        if not isinstance(current, dict):
            raise ToolValidationError(
                "current must be a posture_check-style dict with 'checks'"
            )
        if not isinstance(current.get("checks"), list):
            raise ToolValidationError(
                "current.checks must be a list of {name, state, severity}"
            )
        baseline = args.get("baseline")
        if baseline is not None:
            if not isinstance(baseline, dict):
                raise ToolValidationError(
                    "baseline must be a dict with 'checks' (or omit)"
                )
            if "checks" in baseline and not isinstance(baseline["checks"], list):
                raise ToolValidationError(
                    "baseline.checks must be a list when present"
                )
        esc = args.get("escalate_on_missing")
        if esc is not None and not isinstance(esc, bool):
            raise ToolValidationError(
                "escalate_on_missing must be bool"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        current  = args["current"]
        baseline = args.get("baseline") or {"checks": []}
        escalate = bool(args.get("escalate_on_missing", False))

        cur_by = _index(current.get("checks", []))
        base_by = _index(baseline.get("checks", []))

        added:    list[dict[str, Any]] = []
        removed:  list[dict[str, Any]] = []
        changed:  list[dict[str, Any]] = []
        steady:   list[str] = []

        for name, ent in cur_by.items():
            if name not in base_by:
                added.append({
                    "name":     name,
                    "state":    ent.get("state", "unknown"),
                    "severity": ent.get("severity", "low"),
                })
                continue
            prev = base_by[name]
            if (ent.get("state") != prev.get("state")
                    or ent.get("severity") != prev.get("severity")):
                # Pick the worse of the two severities for the change
                # entry — a low→high transition is HIGH urgency, not
                # average urgency.
                worse = _max_severity([
                    ent.get("severity", "low"),
                    prev.get("severity", "low"),
                ])
                changed.append({
                    "name":     name,
                    "from":     {
                        "state":    prev.get("state"),
                        "severity": prev.get("severity"),
                    },
                    "to":       {
                        "state":    ent.get("state"),
                        "severity": ent.get("severity"),
                    },
                    "severity": worse,
                })
            else:
                steady.append(name)

        for name, prev in base_by.items():
            if name not in cur_by:
                # A check that vanished is suspicious — the probe binary
                # may have been removed or replaced. Caller can opt to
                # treat this as a high-severity finding.
                removed.append({
                    "name":     name,
                    "severity": "high" if escalate else "medium",
                    "prior":    {
                        "state":    prev.get("state"),
                        "severity": prev.get("severity"),
                    },
                })

        # Severity drift = any check's severity changed (independent of
        # state). Useful for skills that gate alerts on severity moves
        # rather than every state flip.
        sev_drift = any(
            c["from"]["severity"] != c["to"]["severity"]
            for c in changed
        )

        # Overall verdict = max severity across all flagged items.
        # Empty diff → low. Note: stable but already-high items don't
        # bump the verdict — only NEW or CHANGED issues do, since the
        # caller has presumably already triaged them.
        all_flagged = added + changed + removed
        verdict = "low"
        if all_flagged:
            verdict = _max_severity([f.get("severity", "low") for f in all_flagged])

        summary = _summarize(added, removed, changed, steady, verdict)

        return ToolResult(
            output={
                "verdict":         verdict,
                "checks_added":    added,
                "checks_removed":  removed,
                "checks_changed":  changed,
                "checks_steady":   steady,
                "severity_drift":  sev_drift,
                "summary":         summary,
            },
            metadata={
                "added_count":   len(added),
                "removed_count": len(removed),
                "changed_count": len(changed),
                "steady_count":  len(steady),
                "had_baseline":  bool(baseline.get("checks")),
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=summary,
        )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _index(checks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index a checks list by name. Last entry wins on duplicates
    (lets a caller patch a single check by appending an override)."""
    out: dict[str, dict[str, Any]] = {}
    for c in checks:
        if not isinstance(c, dict):
            continue
        name = c.get("name")
        if isinstance(name, str) and name:
            out[name] = c
    return out


def _max_severity(sevs: list[str]) -> str:
    max_idx = 0
    for s in sevs:
        try:
            idx = _SEVERITY_ORDER.index(s)
        except ValueError:
            idx = 0  # unknown sev → treat as low
        if idx > max_idx:
            max_idx = idx
    return _SEVERITY_ORDER[max_idx]


def _summarize(
    added:   list[dict[str, Any]],
    removed: list[dict[str, Any]],
    changed: list[dict[str, Any]],
    steady:  list[str],
    verdict: str,
) -> str:
    parts = []
    if added:
        parts.append(f"{len(added)} new")
    if changed:
        parts.append(f"{len(changed)} changed")
    if removed:
        parts.append(f"{len(removed)} removed")
    if not parts:
        return f"steady ({len(steady)} checks, verdict={verdict})"
    return f"drift: {', '.join(parts)}; verdict={verdict}"
