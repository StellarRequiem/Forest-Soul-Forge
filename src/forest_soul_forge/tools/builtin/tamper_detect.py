"""``tamper_detect.v1`` — canary file integrity + macOS SIP probe.

ADR-0033 Phase B3 — privileged-aware. Two complementary signals:

  * **canary integrity** (filesystem) — for each operator-named
    canary path, hash the bytes and compare against a baseline
    digest. Mismatch → finding. New canary (no baseline) → record
    digest for next run. Vanished canary → finding (worse than
    drift; canary deletion implies attempted cleanup).

  * **sip status** (macOS, optional) — when ``probe_sip=true``
    AND ctx.priv_client is wired, probe a SIP-protected file via
    the helper's read-protected op. The probe answers "can the
    helper still read this file?" — a True yes is evidence the
    SIP-protected store hasn't been tampered with. Absence of a
    PrivClient drops this probe to ``skipped`` rather than
    failing.

side_effects=filesystem — the tool only reads files (no writes,
no deletes). Per tool_policy's ``filesystem_always_human_approval``
rule, this still gates on operator approval — the read pattern
itself is what triggers ("which canaries did the agent ask
about?" is information leak we don't want flowing without
approval at high tier).

Refusals (raise ToolValidationError):
  * canaries empty
  * canary path not absolute
  * baseline_digests not a dict
  * probe_sip=true on non-darwin platform → not a refusal,
    just falls into 'skipped' with reason

Output shape composes with ``continuous_verify.v1`` so a skill
can persist the digest map to memory and detect drift across
sweeps.
"""
from __future__ import annotations

import hashlib
import platform
from pathlib import Path
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_MAX_CANARIES = 50
_MAX_FILE_BYTES = 10 * 1024 * 1024   # 10 MiB cap per canary


class TamperDetectTool:
    """Hash a list of canary files; compare to baseline; optionally
    probe SIP via PrivClient.

    Args:
      canaries          (list[str], required): absolute paths of
        files to hash. ≤ 50 entries.
      baseline_digests  (dict[str, str], optional): map of path →
        prior 'sha256:HEX' digest. When provided, the tool
        compares each canary's current digest to its baseline and
        reports {ok, mismatch, new, vanished}. Without a baseline,
        every canary lands in 'new'.
      probe_sip         (bool, optional): on darwin + when
        ctx.priv_client is wired, additionally call
        priv_client.read_protected on a list of operator-named
        sip_paths and verify the helper still reads them.
      sip_paths         (list[str], optional): SIP-protected paths
        to probe. Only used when probe_sip=true.

    Output:
      {
        "canary_results": [
          {"path": str, "status": "ok"|"mismatch"|"new"|"vanished"|"error",
           "digest": str | null, "baseline_digest": str | null,
           "size": int | null, "detail": str | null},
          ...
        ],
        "sip_probes": [
          {"path": str, "ok": bool, "digest": str | null,
           "size": int | null, "detail": str | null},
          ...
        ] | null,
        "verdict":          "ok"|"warn"|"critical",
        "findings_count":   int,
        "skipped":          [{"name": str, "reason": str}, ...],
      }
    """

    name = "tamper_detect"
    version = "1"
    side_effects = "filesystem"

    def validate(self, args: dict[str, Any]) -> None:
        canaries = args.get("canaries")
        if not isinstance(canaries, list) or not canaries:
            raise ToolValidationError(
                "canaries must be a non-empty list of absolute paths"
            )
        if len(canaries) > _MAX_CANARIES:
            raise ToolValidationError(
                f"canaries must be ≤ {_MAX_CANARIES}; got {len(canaries)}"
            )
        for i, p in enumerate(canaries):
            if not isinstance(p, str) or not p:
                raise ToolValidationError(
                    f"canaries[{i}] must be a non-empty string"
                )
            if not p.startswith("/"):
                raise ToolValidationError(
                    f"canaries[{i}] must be an absolute path; got {p!r}"
                )
        baseline = args.get("baseline_digests")
        if baseline is not None and not isinstance(baseline, dict):
            raise ToolValidationError(
                "baseline_digests must be a dict of {path: 'sha256:HEX'}"
            )
        sip_paths = args.get("sip_paths")
        if sip_paths is not None:
            if not isinstance(sip_paths, list):
                raise ToolValidationError(
                    "sip_paths must be a list of absolute paths"
                )
            for i, p in enumerate(sip_paths):
                if not isinstance(p, str) or not p.startswith("/"):
                    raise ToolValidationError(
                        f"sip_paths[{i}] must be an absolute path"
                    )
        probe_sip = args.get("probe_sip")
        if probe_sip is not None and not isinstance(probe_sip, bool):
            raise ToolValidationError("probe_sip must be a bool")

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        canaries  = args["canaries"]
        baseline  = args.get("baseline_digests") or {}
        probe_sip = bool(args.get("probe_sip", False))
        sip_paths = args.get("sip_paths") or []
        skipped:  list[dict[str, str]] = []

        # ---- canary integrity ------------------------------------
        canary_results: list[dict[str, Any]] = []
        for path in canaries:
            entry = _check_canary(path, baseline.get(path))
            canary_results.append(entry)

        # ---- optional SIP probe ----------------------------------
        sip_probe_results: list[dict[str, Any]] | None = None
        if probe_sip:
            plat = platform.system().lower()
            if plat != "darwin":
                skipped.append({
                    "name": "sip",
                    "reason": f"sip probe is darwin-only; platform={plat}",
                })
            elif ctx.priv_client is None:
                skipped.append({
                    "name": "sip",
                    "reason": "no PrivClient wired (helper not installed)",
                })
            elif not sip_paths:
                skipped.append({
                    "name": "sip",
                    "reason": "probe_sip=true but no sip_paths supplied",
                })
            else:
                sip_probe_results = []
                for sp in sip_paths:
                    sip_probe_results.append(_probe_sip(ctx.priv_client, sp))

        # ---- aggregate verdict -----------------------------------
        # Vanished canary → critical (active tampering signal).
        # Mismatch → warn (could be legit edit; investigator decides).
        # New (no baseline) → ok (informational; first-run records).
        # Failed SIP probe → critical.
        findings = [c for c in canary_results
                    if c["status"] in ("mismatch", "vanished", "error")]
        sip_findings = [s for s in (sip_probe_results or [])
                        if not s.get("ok", False)]
        verdict = "ok"
        if any(c["status"] == "vanished" for c in canary_results):
            verdict = "critical"
        elif sip_findings:
            verdict = "critical"
        elif any(c["status"] == "mismatch" for c in canary_results):
            verdict = "warn"

        return ToolResult(
            output={
                "canary_results":  canary_results,
                "sip_probes":      sip_probe_results,
                "verdict":         verdict,
                "findings_count":  len(findings) + len(sip_findings),
                "skipped":         skipped,
            },
            metadata={
                "canary_count":      len(canary_results),
                "had_baseline":      bool(baseline),
                "sip_probe_count":   len(sip_probe_results or []),
                "verdict":           verdict,
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"tamper: {verdict} ({len(findings) + len(sip_findings)} "
                f"finding{'s' if (len(findings) + len(sip_findings)) != 1 else ''})"
            ),
        )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _check_canary(path: str, baseline_digest: str | None) -> dict[str, Any]:
    """Hash one canary; classify against baseline. Never reads more
    than _MAX_FILE_BYTES — a canary that grew too large is itself
    a finding (status='error', detail='oversized')."""
    p = Path(path)
    if not p.exists():
        # Vanished is the worst case: someone rm'd the canary.
        return {
            "path":            path,
            "status":          "vanished",
            "digest":          None,
            "baseline_digest": baseline_digest,
            "size":            None,
            "detail":          "canary file does not exist",
        }
    try:
        st = p.stat()
    except (OSError, PermissionError) as e:
        return {
            "path":            path,
            "status":          "error",
            "digest":          None,
            "baseline_digest": baseline_digest,
            "size":            None,
            "detail":          f"stat failed: {e}",
        }
    if st.st_size > _MAX_FILE_BYTES:
        return {
            "path":            path,
            "status":          "error",
            "digest":          None,
            "baseline_digest": baseline_digest,
            "size":            st.st_size,
            "detail":          f"oversized (> {_MAX_FILE_BYTES} bytes)",
        }
    try:
        h = hashlib.sha256()
        with p.open("rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        digest = "sha256:" + h.hexdigest()
    except (OSError, PermissionError) as e:
        return {
            "path":            path,
            "status":          "error",
            "digest":          None,
            "baseline_digest": baseline_digest,
            "size":            st.st_size,
            "detail":          f"hash read failed: {e}",
        }

    if baseline_digest is None:
        return {
            "path":            path,
            "status":          "new",
            "digest":          digest,
            "baseline_digest": None,
            "size":            st.st_size,
            "detail":          "no baseline; recorded for next sweep",
        }
    status = "ok" if digest == baseline_digest else "mismatch"
    detail = (
        "matches baseline" if status == "ok"
        else f"digest changed from {baseline_digest} to {digest}"
    )
    return {
        "path":            path,
        "status":          status,
        "digest":          digest,
        "baseline_digest": baseline_digest,
        "size":            st.st_size,
        "detail":          detail,
    }


def _probe_sip(priv_client: Any, path: str) -> dict[str, Any]:
    """Hash a SIP-protected file via the helper. Returns an entry
    that mirrors canary_results so the operator can compare both
    streams in one view."""
    from forest_soul_forge.security.priv_client import (
        HelperMissing,
        PrivClient,
        PrivClientError,
    )
    try:
        result = priv_client.read_protected(path)
    except HelperMissing as e:
        return {
            "path":   path, "ok": False, "digest": None, "size": None,
            "detail": f"helper missing: {e}",
        }
    except PrivClientError as e:
        return {
            "path":   path, "ok": False, "digest": None, "size": None,
            "detail": f"client refused: {e}",
        }
    if not result.ok:
        return {
            "path":   path, "ok": False, "digest": None, "size": None,
            "detail": (
                f"helper exit {result.exit_code}: "
                f"{(result.stderr or result.stdout)[:120]}"
            ),
        }
    try:
        digest, size, _path = PrivClient.parse_read_protected_output(
            result.stdout,
        )
    except PrivClientError as e:
        return {
            "path":   path, "ok": False, "digest": None, "size": None,
            "detail": f"output parse: {e}",
        }
    return {
        "path":   path, "ok": True, "digest": digest, "size": size,
        "detail": "helper readable",
    }
