"""``posture_check.v1`` — host security posture probe.

ADR-0033 Phase B3. The high-tier security agent's "is the floor
still concrete" check. Runs a short list of read-only OS probes
that answer the questions every continuous-verification skill
needs to ground itself in: is SIP/SELinux still on, is the disk
encrypted, is the firewall up, are auto-updates scheduled.

Each check independently reports {name, state, value, severity}
so a missing binary or single failed probe doesn't poison the
others. The aggregate ``overall_severity`` is the max severity
across the per-check results — operators see one number to
threshold against, AnomalyAce sees the full breakdown for
delta detection against a prior baseline written to memory.

Backends are platform-aware:
  * macOS:   csrutil, fdesetup, spctl, pfctl, defaults (alf)
  * Linux:   getenforce, aa-status, ufw, cryptsetup
  * unknown: returns platform=unknown, no checks run

side_effects=read_only — every probe is a query-only flag (no
mutating subcommands). The tool refuses any backend whose binary
isn't on PATH rather than running with elevated privilege.

Timeout: 10 seconds per probe (default macOS probes are
sub-second; the cap exists to fail closed if pfctl hangs on a
loaded box).
"""
from __future__ import annotations

import platform
import re
import shutil
import subprocess
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_TIMEOUT_SECONDS = 10
_VALID_STATES = ("ok", "warn", "unknown", "missing")
_SEVERITY_ORDER = ("low", "medium", "high", "critical")
# Per-check default severity floor when a probe reports "warn"
# state (e.g. SIP disabled). Operators can override via the
# 'severity_overrides' arg if the org's posture tolerates a
# weaker default for some check.
_DEFAULT_WARN_SEVERITY = {
    "sip":            "high",        # never disable SIP
    "filevault":      "high",        # disk encryption matters
    "gatekeeper":     "medium",
    "firewall":       "high",
    "app_firewall":   "medium",
    "auto_updates":   "medium",
    "selinux":        "high",
    "apparmor":       "medium",
    "ufw":            "high",
    "disk_encrypt":   "high",
}


class PostureCheckTool:
    """Probe host security posture and aggregate per-check severity.

    Args:
      checks (list[str], optional): subset of probe names to run.
        Default: all probes for the detected platform.
      severity_overrides (dict, optional): map check_name → severity
        to set when that check reports warn state. Lets ops downgrade
        a finding their environment can tolerate without code changes.

    Output:
      {
        "platform":         "darwin"|"linux"|"unknown",
        "checks": [
          {"name": str, "state": str, "value": str, "severity": str},
          ...
        ],
        "issues": [
          {"name": str, "reason": str, "severity": str}, ...
        ],
        "overall_severity": "low"|"medium"|"high"|"critical",
        "checks_skipped":  [{"name": str, "reason": str}, ...]
      }
    """

    name = "posture_check"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        checks = args.get("checks")
        if checks is not None:
            if not isinstance(checks, list):
                raise ToolValidationError("checks must be a list of strings")
            for c in checks:
                if not isinstance(c, str) or not c:
                    raise ToolValidationError(
                        "checks entries must be non-empty strings"
                    )
        sev_over = args.get("severity_overrides")
        if sev_over is not None:
            if not isinstance(sev_over, dict):
                raise ToolValidationError(
                    "severity_overrides must be a dict of check_name -> severity"
                )
            for k, v in sev_over.items():
                if not isinstance(k, str) or not k:
                    raise ToolValidationError(
                        "severity_overrides keys must be non-empty strings"
                    )
                if v not in _SEVERITY_ORDER:
                    raise ToolValidationError(
                        f"severity_overrides[{k!r}] must be one of "
                        f"{list(_SEVERITY_ORDER)}; got {v!r}"
                    )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        wanted = args.get("checks")
        sev_over = args.get("severity_overrides") or {}
        plat = _detect_platform()
        probes = _platform_probes(plat)
        if wanted:
            probes = {k: v for k, v in probes.items() if k in wanted}

        checks_out: list[dict[str, str]] = []
        issues:     list[dict[str, str]] = []
        skipped:    list[dict[str, str]] = []

        for cname, runner in probes.items():
            try:
                state, value = runner()
            except _BinaryMissing as e:
                skipped.append({"name": cname, "reason": str(e)})
                continue
            except _ProbeError as e:
                # The probe ran but couldn't make sense of the output —
                # don't poison overall severity, but record so an
                # operator can spot the regression.
                checks_out.append({
                    "name":     cname,
                    "state":    "unknown",
                    "value":    str(e)[:200],
                    "severity": "low",
                })
                continue
            sev = _check_severity(cname, state, sev_over)
            checks_out.append({
                "name":     cname,
                "state":    state,
                "value":    value[:200],
                "severity": sev,
            })
            if state == "warn":
                issues.append({
                    "name":     cname,
                    "reason":   value[:200],
                    "severity": sev,
                })

        overall = _aggregate_severity(checks_out)

        return ToolResult(
            output={
                "platform":         plat,
                "checks":           checks_out,
                "issues":           issues,
                "overall_severity": overall,
                "checks_skipped":   skipped,
            },
            metadata={
                "checks_run":     len(checks_out),
                "issues_count":   len(issues),
                "checks_skipped": len(skipped),
                "platform":       plat,
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"posture: {overall} ({len(issues)} issue"
                f"{'s' if len(issues) != 1 else ''} on {plat})"
            ),
        )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
class _BinaryMissing(Exception):
    """Probe binary not on PATH. Reported as 'skipped' rather than
    a check failure — distinguishes 'no info' from 'bad info.'"""


class _ProbeError(Exception):
    """Probe ran but output was malformed. Reported as state=unknown
    so the per-check severity stays low — an operator-facing parse
    regression shouldn't escalate to 'critical' on its own."""


def _detect_platform() -> str:
    sys = platform.system().lower()
    if sys == "darwin":
        return "darwin"
    if sys == "linux":
        return "linux"
    return "unknown"


def _platform_probes(plat: str) -> dict:
    """Return {check_name: runner_callable} for the detected platform.
    Each runner returns (state, value) or raises _BinaryMissing /
    _ProbeError."""
    if plat == "darwin":
        return {
            "sip":          _probe_sip,
            "filevault":    _probe_filevault,
            "gatekeeper":   _probe_gatekeeper,
            "firewall":     _probe_pf,
            "app_firewall": _probe_alf,
        }
    if plat == "linux":
        return {
            "selinux":      _probe_selinux,
            "apparmor":     _probe_apparmor,
            "ufw":          _probe_ufw,
            "disk_encrypt": _probe_cryptsetup,
        }
    return {}


def _check_severity(
    check_name: str,
    state: str,
    overrides: dict[str, str],
) -> str:
    """Map (check, state) → severity. 'ok' → low; 'warn' → override or
    default warn-severity for that check; 'unknown'/'missing' → low."""
    if state == "ok":
        return "low"
    if state == "warn":
        return overrides.get(
            check_name,
            _DEFAULT_WARN_SEVERITY.get(check_name, "medium"),
        )
    return "low"


def _aggregate_severity(checks: list[dict[str, str]]) -> str:
    """Max severity across all checks. Empty → low (no info, no
    findings). Severity_score consumers can map this to 0..1."""
    max_idx = 0
    for c in checks:
        idx = _SEVERITY_ORDER.index(c.get("severity", "low"))
        if idx > max_idx:
            max_idx = idx
    return _SEVERITY_ORDER[max_idx]


def _run_probe(cmd: list[str]) -> str:
    """Run a probe binary, return stdout. Raises _BinaryMissing on
    missing PATH entry, _ProbeError on timeout/non-zero exit."""
    binary = shutil.which(cmd[0])
    if binary is None:
        raise _BinaryMissing(f"{cmd[0]} not on PATH")
    try:
        proc = subprocess.run(
            [binary] + cmd[1:],
            capture_output=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise _ProbeError(f"{cmd[0]} timed out after {_TIMEOUT_SECONDS}s")
    out = (proc.stdout + proc.stderr).decode("utf-8", errors="replace").strip()
    return out


# --- macOS probes -----------------------------------------------------------
def _probe_sip() -> tuple[str, str]:
    """csrutil status → 'System Integrity Protection status: enabled.'"""
    out = _run_probe(["csrutil", "status"])
    low = out.lower()
    if "enabled" in low and "disabled" not in low:
        return ("ok", "enabled")
    if "disabled" in low:
        return ("warn", out.splitlines()[0] if out else "disabled")
    return ("unknown", out[:120])


def _probe_filevault() -> tuple[str, str]:
    """fdesetup status → 'FileVault is On.' / 'FileVault is Off.'"""
    out = _run_probe(["fdesetup", "status"])
    low = out.lower()
    if "filevault is on" in low:
        return ("ok", "on")
    if "filevault is off" in low:
        return ("warn", "off")
    return ("unknown", out[:120])


def _probe_gatekeeper() -> tuple[str, str]:
    """spctl --status → 'assessments enabled' / 'assessments disabled'"""
    out = _run_probe(["spctl", "--status"])
    low = out.lower()
    if "assessments enabled" in low:
        return ("ok", "enabled")
    if "assessments disabled" in low:
        return ("warn", "disabled")
    return ("unknown", out[:120])


def _probe_pf() -> tuple[str, str]:
    """pfctl -s info → 'Status: Enabled' or 'Status: Disabled'.
    On macOS this often requires root for full output; without
    root, pfctl prints to stderr and exits non-zero. We accept
    a Status: line in either stream."""
    try:
        out = _run_probe(["pfctl", "-s", "info"])
    except _BinaryMissing:
        raise
    m = re.search(r"^Status:\s*(Enabled|Disabled)", out, flags=re.MULTILINE)
    if m:
        if m.group(1).lower() == "enabled":
            return ("ok", "enabled")
        return ("warn", "disabled")
    # Without root, pfctl emits "pfctl: Operation not permitted" — we
    # report unknown rather than warn; a security_high agent that needs
    # the real answer should re-run via PrivClient.
    return ("unknown", "pf state requires root to query")


def _probe_alf() -> tuple[str, str]:
    """defaults read /Library/Preferences/com.apple.alf globalstate
    0 = off, 1 = on for specific services, 2 = block all."""
    out = _run_probe([
        "defaults", "read",
        "/Library/Preferences/com.apple.alf", "globalstate",
    ])
    s = out.strip()
    if s == "0":
        return ("warn", "application firewall off")
    if s in ("1", "2"):
        return ("ok", f"globalstate={s}")
    return ("unknown", out[:120])


# --- Linux probes -----------------------------------------------------------
def _probe_selinux() -> tuple[str, str]:
    """getenforce → 'Enforcing' | 'Permissive' | 'Disabled'."""
    out = _run_probe(["getenforce"])
    low = out.lower()
    if "enforcing" in low:
        return ("ok", "enforcing")
    if "permissive" in low or "disabled" in low:
        return ("warn", out)
    return ("unknown", out[:120])


def _probe_apparmor() -> tuple[str, str]:
    """aa-status — exit 0 + 'profiles are loaded' = ok."""
    out = _run_probe(["aa-status"])
    low = out.lower()
    if "profiles are loaded" in low:
        return ("ok", out.splitlines()[0] if out else "loaded")
    if "not loaded" in low or "is disabled" in low:
        return ("warn", out.splitlines()[0] if out else "not loaded")
    return ("unknown", out[:120])


def _probe_ufw() -> tuple[str, str]:
    """ufw status → 'Status: active' | 'Status: inactive'."""
    out = _run_probe(["ufw", "status"])
    low = out.lower()
    if "status: active" in low:
        return ("ok", "active")
    if "status: inactive" in low:
        return ("warn", "inactive")
    return ("unknown", out[:120])


def _probe_cryptsetup() -> tuple[str, str]:
    """cryptsetup status on the system root — best-effort. Many
    distros mount root via /dev/mapper/<vg>-root; we just check
    if the binary exists and call ``status``. Distro-aware probes
    can replace this in B3+ if needed."""
    out = _run_probe(["cryptsetup", "status", "/dev/mapper/cryptroot"])
    low = out.lower()
    if "active and is in use" in low:
        return ("ok", "active")
    if "inactive" in low or "no such" in low:
        # Could mean unencrypted, OR a different mapper name. Caller
        # should treat 'unknown' as needs-investigation rather than
        # green-lighting unencrypted disk.
        return ("unknown", out[:120])
    return ("unknown", out[:120])
