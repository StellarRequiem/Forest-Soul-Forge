"""``canary_token.v1`` — plant + check honeytokens with dual signals.

ADR-0033 Phase B3. CanaryWarden's trap-laying tool. Plants
operator-named decoy files at chosen paths and, on subsequent
sweeps, reports any token that was *touched* — either accessed
(atime moved) or modified (content hash changed) since the last
check. Either signal is suspicious: an attacker rifling
through a 'credentials' file produces an atime drift; an attacker
swapping the file for a working credential produces a hash drift.

**Dual-signal design:** atime alone misses modifications and is
unreliable on noatime mounts (common on macOS APFS); hash alone
misses pure read access. Together they cover the full set of
"someone interacted with this token" cases. When atime tracking
is unavailable for a path (mount option, filesystem support),
the per-token entry reports atime_capability='unavailable' so
the operator knows that token degrades to hash-only.

**Operations:**
  * ``plant``   — write each named token to disk (using
    operator-supplied content or a generated default), record
    the SHA-256 + size + atime in the output. Caller persists
    that output to memory at scope='lineage' as the baseline
    for the next sweep.
  * ``check``   — re-stat + re-hash each token against a
    baseline. Per-token findings:
      - **untouched** (default): atime matches baseline AND hash matches
      - **accessed**:  atime moved later than baseline; hash unchanged
      - **modified**:  hash changed (covers append, replace, partial overwrite)
      - **vanished**:  file no longer exists
      - **error**:     stat/hash failed (permissions, oversized)

side_effects=filesystem — plant writes; check only reads. Per
tool_policy's ``filesystem_always_human_approval`` rule, both
ops gate on operator approval at high tier — exactly right for
trap placement (which paths get planted is information you don't
want flowing without review).

Caps: 50 tokens per call. 4 KiB per token content (honeytokens
are small by design). 4 MiB per file when re-hashing on check
(a token that grew is itself a finding).
"""
from __future__ import annotations

import hashlib
import secrets
from pathlib import Path
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_VALID_OPS = ("plant", "check")
_MAX_TOKENS = 50
_MAX_CONTENT_BYTES = 4 * 1024            # 4 KiB
_MAX_RECHECK_BYTES = 4 * 1024 * 1024     # 4 MiB
# Default token body shape: a fake AWS-style key + a fake
# ed25519 fingerprint. Operators can override per-token via
# the 'content' field. The default is intentionally
# recognisable so an analyst seeing it in egress traffic
# instantly knows "that's a planted token, not a real key."
_DEFAULT_PREFIX = "FSF-CANARY-DO-NOT-USE"


class CanaryTokenTool:
    """Plant or check honeytokens — atime + hash dual-signal.

    Args:
      op       (str, required): one of 'plant', 'check'.
      tokens   (list[dict], required): each {path, content?,
        label?}. ``path`` is absolute. ``content`` is optional
        token body (≤ 4 KiB); when omitted, a default
        recognisable canary string is planted. ``label`` is an
        operator tag echoed in output for cross-referencing.
      baseline (dict, required for op=check): map of path →
        {hash, atime_unix, size}. Output of a prior op=plant
        call. Without it, op=check refuses (would have nothing
        to compare against).
      atime_drift_seconds (int, optional, default 0): tolerance
        for spurious atime updates from periodic system scans.
        atime moving by ≤ this many seconds is treated as
        untouched. Set to 0 (default) for strictest signal.

    Output:
      {
        "op":              "plant"|"check",
        "results": [
          {"path":..., "label":..., "status":..., "hash":...,
           "atime_unix":..., "size":..., "atime_capability":...,
           "baseline_hash":..., "baseline_atime_unix":...,
           "detail":...},
          ...
        ],
        "verdict":         "ok"|"warn"|"critical",
        "findings_count":  int,
      }
    """

    name = "canary_token"
    version = "1"
    side_effects = "filesystem"

    def validate(self, args: dict[str, Any]) -> None:
        op = args.get("op")
        if op not in _VALID_OPS:
            raise ToolValidationError(
                f"op must be one of {list(_VALID_OPS)}; got {op!r}"
            )
        tokens = args.get("tokens")
        if not isinstance(tokens, list) or not tokens:
            raise ToolValidationError(
                "tokens must be a non-empty list of {path, content?, label?}"
            )
        if len(tokens) > _MAX_TOKENS:
            raise ToolValidationError(
                f"tokens must be ≤ {_MAX_TOKENS}; got {len(tokens)}"
            )
        seen_paths: set[str] = set()
        for i, t in enumerate(tokens):
            if not isinstance(t, dict):
                raise ToolValidationError(
                    f"tokens[{i}] must be a dict"
                )
            p = t.get("path")
            if not isinstance(p, str) or not p.startswith("/"):
                raise ToolValidationError(
                    f"tokens[{i}].path must be an absolute string"
                )
            if p in seen_paths:
                raise ToolValidationError(
                    f"duplicate path in tokens: {p}"
                )
            seen_paths.add(p)
            if "content" in t:
                c = t["content"]
                if not isinstance(c, str):
                    raise ToolValidationError(
                        f"tokens[{i}].content must be a string"
                    )
                if len(c.encode("utf-8")) > _MAX_CONTENT_BYTES:
                    raise ToolValidationError(
                        f"tokens[{i}].content must be ≤ "
                        f"{_MAX_CONTENT_BYTES} bytes"
                    )
            if "label" in t and not isinstance(t["label"], str):
                raise ToolValidationError(
                    f"tokens[{i}].label must be a string"
                )
        if op == "check":
            baseline = args.get("baseline")
            if not isinstance(baseline, dict):
                raise ToolValidationError(
                    "op=check requires 'baseline' dict (output of a "
                    "prior op=plant call). Without baseline, the tool "
                    "has nothing to compare against."
                )
        drift = args.get("atime_drift_seconds")
        if drift is not None:
            if not isinstance(drift, int) or isinstance(drift, bool):
                raise ToolValidationError(
                    "atime_drift_seconds must be an integer ≥ 0"
                )
            if drift < 0:
                raise ToolValidationError(
                    "atime_drift_seconds must be ≥ 0"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        op = args["op"]
        tokens = args["tokens"]
        baseline = args.get("baseline") or {}
        drift_tolerance = int(args.get("atime_drift_seconds", 0))

        results: list[dict[str, Any]] = []
        if op == "plant":
            for t in tokens:
                results.append(_plant_token(t))
        else:  # op == check
            for t in tokens:
                base = baseline.get(t["path"])
                results.append(_check_token(t, base, drift_tolerance))

        # Verdict aggregation. Worst case wins.
        # vanished or modified → critical; accessed → warn; otherwise ok.
        statuses = {r["status"] for r in results}
        verdict = "ok"
        if "vanished" in statuses or "modified" in statuses:
            verdict = "critical"
        elif "accessed" in statuses:
            verdict = "warn"
        findings = [r for r in results
                    if r["status"] in ("accessed", "modified",
                                       "vanished", "error")]

        return ToolResult(
            output={
                "op":             op,
                "results":        results,
                "verdict":        verdict,
                "findings_count": len(findings),
            },
            metadata={
                "op":            op,
                "token_count":   len(tokens),
                "verdict":       verdict,
                "had_baseline":  bool(baseline),
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"canary {op}: {verdict} "
                f"({len(findings)} finding{'s' if len(findings) != 1 else ''})"
            ),
        )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _default_content(label: str | None) -> str:
    """Generate a recognisable canary body. The token name + a
    cryptographic random suffix so two tokens planted in different
    sweeps don't collide."""
    suffix = secrets.token_hex(16)
    tag = label or "token"
    return (
        f"{_DEFAULT_PREFIX}\n"
        f"label={tag}\n"
        f"id={suffix}\n"
        f"# This file is a planted decoy. Touching it triggers an alert.\n"
        f"# Any system actually reading this for credentials has been compromised.\n"
    )


def _plant_token(t: dict[str, Any]) -> dict[str, Any]:
    """Write the token's content to disk; record hash + atime + size."""
    path = t["path"]
    label = t.get("label")
    body = t.get("content") or _default_content(label)
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    except (OSError, PermissionError) as e:
        return {
            "path":             path,
            "label":            label,
            "status":           "error",
            "hash":             None,
            "atime_unix":       None,
            "size":             None,
            "atime_capability": "unknown",
            "detail":           f"plant failed: {e}",
        }
    try:
        st = p.stat()
        # Read-back hash so the planted bytes match what we
        # actually got on disk (filesystem may have done line
        # ending conversion or similar). Cheap — files are tiny.
        digest = "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()
    except (OSError, PermissionError) as e:
        return {
            "path":             path,
            "label":            label,
            "status":           "error",
            "hash":             None,
            "atime_unix":       None,
            "size":             None,
            "atime_capability": "unknown",
            "detail":           f"post-plant stat/hash failed: {e}",
        }
    return {
        "path":             path,
        "label":            label,
        "status":           "planted",
        "hash":             digest,
        "atime_unix":       int(st.st_atime),
        "size":             st.st_size,
        # Capability detection deferred to the first 'check' call —
        # we have no way to test atime sensitivity from a single
        # plant. Reported as 'unknown' here; check fills it in.
        "atime_capability": "unknown",
        "detail":           "planted; record this entry in baseline for next check",
    }


def _check_token(
    t: dict[str, Any],
    baseline: dict[str, Any] | None,
    drift_tolerance: int,
) -> dict[str, Any]:
    """Re-stat + re-hash one token; classify against baseline."""
    path = t["path"]
    label = t.get("label")
    p = Path(path)

    # No baseline for this token → can't classify; treat as new
    # informational. (Distinct from 'planted' so the operator can
    # see "you asked me to check a token I have no record of.")
    if baseline is None:
        return {
            "path":             path,
            "label":            label,
            "status":           "no_baseline",
            "hash":             None,
            "atime_unix":       None,
            "size":             None,
            "atime_capability": "unknown",
            "baseline_hash":    None,
            "baseline_atime_unix": None,
            "detail":           "no baseline entry; cannot classify",
        }

    if not p.exists():
        return {
            "path":             path,
            "label":            label,
            "status":           "vanished",
            "hash":             None,
            "atime_unix":       None,
            "size":             None,
            "atime_capability": "unknown",
            "baseline_hash":    baseline.get("hash"),
            "baseline_atime_unix": baseline.get("atime_unix"),
            "detail":           "canary file no longer exists",
        }

    try:
        st = p.stat()
    except (OSError, PermissionError) as e:
        return {
            "path":             path,
            "label":            label,
            "status":           "error",
            "hash":             None,
            "atime_unix":       None,
            "size":             None,
            "atime_capability": "unknown",
            "baseline_hash":    baseline.get("hash"),
            "baseline_atime_unix": baseline.get("atime_unix"),
            "detail":           f"stat failed: {e}",
        }
    if st.st_size > _MAX_RECHECK_BYTES:
        return {
            "path":             path,
            "label":            label,
            "status":           "error",
            "hash":             None,
            "atime_unix":       int(st.st_atime),
            "size":             st.st_size,
            "atime_capability": "unknown",
            "baseline_hash":    baseline.get("hash"),
            "baseline_atime_unix": baseline.get("atime_unix"),
            "detail":           f"oversized for re-hash (> {_MAX_RECHECK_BYTES} bytes)",
        }
    try:
        digest = "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()
    except (OSError, PermissionError) as e:
        return {
            "path":             path,
            "label":            label,
            "status":           "error",
            "hash":             None,
            "atime_unix":       int(st.st_atime),
            "size":             st.st_size,
            "atime_capability": "unknown",
            "baseline_hash":    baseline.get("hash"),
            "baseline_atime_unix": baseline.get("atime_unix"),
            "detail":           f"hash read failed: {e}",
        }

    base_hash  = baseline.get("hash")
    base_atime = baseline.get("atime_unix") or 0
    cur_atime  = int(st.st_atime)
    atime_drift = cur_atime - base_atime

    # Detect atime capability heuristically: if atime equals
    # baseline atime to the second AND we're about to flag this
    # as 'untouched' on hash too, the mount may still be
    # tracking — we just have nothing to prove it. A genuine
    # noatime mount produces atime exactly == mtime forever; we
    # surface that as 'unavailable' so the operator knows
    # this token degrades to hash-only.
    atime_capability = (
        "unavailable" if cur_atime == int(st.st_mtime) and atime_drift == 0
        else "available"
    )

    # Classification order matters: modification beats access
    # (both could be true; the worse signal wins for verdict).
    if base_hash is not None and digest != base_hash:
        status = "modified"
        detail = f"hash changed: {base_hash} → {digest}"
    elif atime_drift > drift_tolerance:
        status = "accessed"
        detail = (
            f"atime advanced by {atime_drift}s "
            f"(tolerance {drift_tolerance}s)"
        )
    else:
        status = "untouched"
        detail = "matches baseline; no drift"

    return {
        "path":             path,
        "label":            label,
        "status":           status,
        "hash":             digest,
        "atime_unix":       cur_atime,
        "size":             st.st_size,
        "atime_capability": atime_capability,
        "baseline_hash":    base_hash,
        "baseline_atime_unix": base_atime,
        "detail":           detail,
    }
