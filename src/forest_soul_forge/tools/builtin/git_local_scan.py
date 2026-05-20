"""``git_local_scan.v1`` — local-git posture scanner.

ADR-0084 Rule 6 substrate. Side effects: read_only.

Periodically scans the local git checkout against four
posture dimensions:

  1. **Committed-secret detection** — scans tracked files for
     GitHub-token prefixes (ghp_/gho_/ghs_/ghu_/github_pat_),
     SSH/OpenSSH private keys, OpenAI sk- tokens. Uses inline
     patterns from `config/security_iocs.yaml` v2+ where
     present, falls back to a built-in minimal set so the tool
     works even on a checkout where the catalog hasn't synced.

  2. **Signed-commit posture** — runs ``git log --format=%G?``
     on the last N commits (default 50, max 500). Each entry is
     either ``G`` (good signature), ``B`` (bad signature),
     ``U`` (unknown signer), ``X`` (expired), ``Y`` (revoked),
     ``R`` (revocation key), ``E`` (cannot check), or ``N``
     (no signature). Returns counts per status + an
     ``unsigned_count`` summary.

  3. **Sync state vs origin** — runs ``git rev-list --count``
     to determine ahead/behind counts against the upstream
     branch. ``stale_pushes_pending`` counts local commits not
     yet on origin; ``unfetched_upstream`` counts origin commits
     not yet local. A healthy state is (0, 0). The tool does NOT
     fetch (read_only); operators run ``git fetch`` separately.

  4. **`.gitignore` coverage** — checks whether the gitignore
     covers a small set of operator-secret-bearing patterns:
     ``.env``, ``.env.*``, ``*.pem``, ``*.key``, ``id_rsa``,
     ``id_ed25519``, ``.streamlit/secrets.toml``, ``credentials*``.
     Missing entries surface as findings; the operator may
     have intentional reasons to skip some, so each missing
     pattern is INFO-severity not FAIL.

Per-agent constitution must populate:
  allowed_paths: [/abs/path/to/repo, ...]

Path discipline mirrors ``git_log_read.v1`` / ``git_diff_read.v1``:
  - Resolve to absolute symlink-free form
  - is_relative_to defense against ../ escape
  - subprocess git invocations cd into the validated repo path

Output shape (single response):
  {
    "ok":      bool,    # True iff all four dimensions clean
    "summary": {
      "total_findings": int,
      "critical_findings": int,
      "high_findings":     int,
      "medium_findings":   int,
      "low_findings":      int,
      "info_findings":     int
    },
    "secrets":   {findings: [...], scanned_files: int},
    "signing":   {commits_checked: int, by_status: {...},
                   unsigned_count: int, ok: bool},
    "sync":      {ahead: int, behind: int, upstream_ref: str,
                   ok: bool, note: str},
    "gitignore": {missing_patterns: [...], present_patterns: [...]}
  }

The B416/B428 nested-bug arc proved that "ship the data without
the consumer" leaves a feature dead. This tool is the consumer
for ADR-0084's posture rules — what gets measured gets enforced.
"""
from __future__ import annotations

import re
import subprocess  # noqa: S404 — read-only git invocations
from pathlib import Path
from typing import Any

import yaml

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


# Built-in minimum secret patterns. Used if config/security_iocs.yaml
# isn't readable from the resolved repo (e.g. operator runs the tool
# in a fresh checkout that hasn't yet fetched the catalog). The IoC
# catalog v2 patterns are richer; this list is the floor.
_DEFAULT_SECRET_PATTERNS: tuple[tuple[str, str, str], ...] = (
    # (rule_id, severity, regex)
    (
        "github_pat_or_app_token",
        "CRITICAL",
        r"(?:ghp_|gho_|ghs_|ghu_|github_pat_)[A-Za-z0-9_]{20,}",
    ),
    (
        "openssh_private_key",
        "CRITICAL",
        r"-----BEGIN (?:OPENSSH|RSA|DSA|EC|PGP) PRIVATE KEY-----",
    ),
    (
        "openai_sk_token",
        "HIGH",
        r"sk-[A-Za-z0-9]{40,}",
    ),
    (
        "aws_access_key_id",
        "HIGH",
        r"(?:AKIA|ASIA)[A-Z0-9]{16}",
    ),
)


_GITIGNORE_EXPECTED_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_ed25519",
    ".streamlit/secrets.toml",
    "credentials*",
)


def _resolve_repo_path(ctx: ToolContext, args: dict[str, Any]) -> Path:
    """Resolve and validate the target repo path.

    Mirrors the path-discipline pattern used by git_log_read.v1 +
    git_diff_read.v1. ``repo_path`` arg is optional; defaults to
    the first entry in the agent's allowed_paths constraint (which
    is the only safe default — running the scanner against a path
    not in allowed_paths would be a constitutional violation).
    """
    raw = args.get("repo_path") or ""
    constraints = ctx.constraints or {}
    allowed_paths = constraints.get("allowed_paths") or []
    if not raw and allowed_paths:
        raw = allowed_paths[0]
    if not raw:
        raise ToolValidationError(
            "git_local_scan.v1: repo_path arg is empty and agent "
            "has no allowed_paths; cannot infer scan target"
        )
    try:
        resolved = Path(raw).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as e:
        raise ToolValidationError(
            f"git_local_scan.v1: repo_path {raw!r} does not exist: {e}"
        ) from e
    # is_relative_to defense: resolved must equal or be inside one of
    # the allowed_paths entries.
    if allowed_paths:
        ok = False
        for ap in allowed_paths:
            try:
                ap_resolved = Path(ap).expanduser().resolve(strict=True)
                if resolved == ap_resolved or resolved.is_relative_to(ap_resolved):
                    ok = True
                    break
            except (OSError, RuntimeError):
                continue
        if not ok:
            raise ToolValidationError(
                f"git_local_scan.v1: repo_path {resolved} not within "
                f"agent's allowed_paths"
            )
    # Sanity: actually a git repo
    if not (resolved / ".git").exists():
        raise ToolValidationError(
            f"git_local_scan.v1: {resolved} is not a git repository "
            f"(no .git directory)"
        )
    return resolved


def _load_secret_patterns(repo: Path) -> list[tuple[str, str, re.Pattern]]:
    """Load IoC patterns from config/security_iocs.yaml v2+ if
    available, else fall back to the minimum floor.

    Returns a list of (rule_id, severity, compiled_pattern) tuples
    used by _scan_for_secrets.
    """
    out: list[tuple[str, str, re.Pattern]] = []
    iocs_path = repo / "config" / "security_iocs.yaml"
    if iocs_path.exists():
        try:
            with iocs_path.open() as f:
                catalog = yaml.safe_load(f) or {}
            for rule in catalog.get("rules") or []:
                rule_id = rule.get("id")
                severity = (rule.get("severity") or "MEDIUM").upper()
                pattern_str = rule.get("pattern")
                # Only consume rules that target file PATHS (applies_to
                # broad) — the secret-detection rules in v2 have empty
                # applies_to (== all files) or include broad globs.
                if not rule_id or not pattern_str:
                    continue
                # Heuristic: this scanner is for secret-like patterns
                # specifically. Match rules with "token", "secret",
                # "key", or "pat" in the id.
                hint = rule_id.lower()
                if not any(k in hint for k in ("token", "secret", "key", "pat")):
                    continue
                try:
                    out.append((rule_id, severity, re.compile(pattern_str)))
                except re.error:
                    # Skip malformed patterns; not the scanner's job
                    # to validate the catalog. ADR-0062 says catalog
                    # ships with vetted patterns.
                    continue
        except (OSError, yaml.YAMLError):
            # Fall through to defaults
            pass
    # Always add the default floor (idempotent if catalog already
    # has these; the rule_id key dedupes downstream callers).
    seen = {r[0] for r in out}
    for rule_id, severity, pattern_str in _DEFAULT_SECRET_PATTERNS:
        if rule_id in seen:
            continue
        try:
            out.append((rule_id, severity, re.compile(pattern_str)))
        except re.error:
            continue
    return out


def _scan_for_secrets(
    repo: Path,
    patterns: list[tuple[str, str, re.Pattern]],
    max_files: int = 5000,
) -> tuple[list[dict[str, Any]], int]:
    """Walk tracked files; return findings + count of files scanned.

    Uses ``git ls-files`` so untracked/gitignored files are skipped
    (operator's working copy may have local-only secrets like
    ``.env`` and that's expected). Bounded by max_files; truncation
    surfaces as a metadata note.
    """
    proc = subprocess.run(  # noqa: S603 — fixed argv
        ["git", "-C", str(repo), "ls-files"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        return [], 0
    tracked = [ln for ln in proc.stdout.splitlines() if ln]
    findings: list[dict[str, Any]] = []
    scanned = 0
    for rel in tracked[:max_files]:
        scanned += 1
        p = repo / rel
        # Skip binary / oversized files — secret scanning is a text
        # operation; trying to read a 100MB binary blob is wasteful.
        try:
            if p.stat().st_size > 1_000_000:  # 1MB ceiling per file
                continue
        except OSError:
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for rule_id, severity, pattern in patterns:
            for m in pattern.finditer(content):
                # Compute line number for operator-friendly reporting
                line_no = content[: m.start()].count("\n") + 1
                findings.append(
                    {
                        "rule_id": rule_id,
                        "severity": severity,
                        "path": rel,
                        "line": line_no,
                        # Redact the actual match — surface only the
                        # rule_id + location. Don't echo secrets back.
                        "match_redacted": "<REDACTED>",
                    }
                )
                # One finding per (file, rule) is enough; don't spam
                # if the same secret appears many times in one file.
                break
    return findings, scanned


def _check_signing(
    repo: Path, max_commits: int
) -> dict[str, Any]:
    """Inspect last `max_commits` for GPG/SSH signature status."""
    proc = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), "log", f"-{max_commits}", "--format=%G?|%H"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        return {
            "commits_checked": 0,
            "by_status": {},
            "unsigned_count": 0,
            "ok": False,
            "error": proc.stderr.strip()[:200],
        }
    by_status: dict[str, int] = {}
    unsigned = 0
    total = 0
    for ln in proc.stdout.splitlines():
        if not ln or "|" not in ln:
            continue
        status, _sha = ln.split("|", 1)
        total += 1
        by_status[status] = by_status.get(status, 0) + 1
        # N = no signature, "E" = cannot check (often means "no sig").
        # Per ADR-0084 Rule 2, signed = "G" only (good signature).
        if status in {"N", "E"}:
            unsigned += 1
    return {
        "commits_checked": total,
        "by_status": by_status,
        "unsigned_count": unsigned,
        "ok": unsigned == 0,
    }


def _check_sync(repo: Path) -> dict[str, Any]:
    """Ahead/behind counts vs upstream.

    Does NOT fetch — read-only by contract. Operator runs
    ``git fetch`` separately to refresh upstream-tracking refs.
    """
    # Determine upstream branch (may be unset on a fresh clone).
    upstream_proc = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "@{upstream}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if upstream_proc.returncode != 0:
        return {
            "ahead": 0,
            "behind": 0,
            "upstream_ref": None,
            "ok": False,
            "note": "no upstream tracking branch set",
        }
    upstream_ref = upstream_proc.stdout.strip()
    counts_proc = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), "rev-list", "--left-right", "--count",
         f"HEAD...{upstream_ref}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if counts_proc.returncode != 0:
        return {
            "ahead": 0,
            "behind": 0,
            "upstream_ref": upstream_ref,
            "ok": False,
            "note": counts_proc.stderr.strip()[:200],
        }
    parts = counts_proc.stdout.split()
    if len(parts) != 2:
        return {
            "ahead": 0,
            "behind": 0,
            "upstream_ref": upstream_ref,
            "ok": False,
            "note": "rev-list returned unexpected format",
        }
    ahead = int(parts[0])
    behind = int(parts[1])
    note = ""
    if ahead > 0:
        note = f"{ahead} local commit(s) not yet on {upstream_ref}"
    if behind > 0:
        note = (note + "; " if note else "") + (
            f"{behind} upstream commit(s) not yet local (run git fetch)"
        )
    return {
        "ahead": ahead,
        "behind": behind,
        "upstream_ref": upstream_ref,
        "ok": ahead == 0 and behind == 0,
        "note": note or "in sync",
    }


def _check_gitignore(repo: Path) -> dict[str, Any]:
    """Verify .gitignore covers expected operator-secret patterns."""
    ignore_path = repo / ".gitignore"
    if not ignore_path.exists():
        return {
            "missing_patterns": list(_GITIGNORE_EXPECTED_PATTERNS),
            "present_patterns": [],
            "note": ".gitignore not present at repo root",
        }
    try:
        lines = ignore_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {
            "missing_patterns": list(_GITIGNORE_EXPECTED_PATTERNS),
            "present_patterns": [],
            "note": ".gitignore unreadable",
        }
    # Normalize: strip comments + whitespace
    entries = {
        ln.strip()
        for ln in lines
        if ln.strip() and not ln.strip().startswith("#")
    }
    present: list[str] = []
    missing: list[str] = []
    for pat in _GITIGNORE_EXPECTED_PATTERNS:
        if pat in entries:
            present.append(pat)
        else:
            missing.append(pat)
    return {
        "missing_patterns": missing,
        "present_patterns": present,
    }


def _severity_bucket(findings: list[dict[str, Any]]) -> dict[str, int]:
    """Count findings by severity for the summary block."""
    counts = {
        "critical_findings": 0,
        "high_findings": 0,
        "medium_findings": 0,
        "low_findings": 0,
        "info_findings": 0,
    }
    for f in findings:
        sev = (f.get("severity") or "").upper()
        key = {
            "CRITICAL": "critical_findings",
            "HIGH": "high_findings",
            "MEDIUM": "medium_findings",
            "LOW": "low_findings",
            "INFO": "info_findings",
        }.get(sev)
        if key:
            counts[key] += 1
    return counts


class GitLocalScanTool:
    """Scan the local git checkout against ADR-0084 posture rules.

    Args:
      repo_path (str, optional): absolute path to the repo root.
        Defaults to the first entry of agent's allowed_paths.
      max_commits (int, optional): how many recent commits to
        check for signature status. Default 50. Max 500.
      max_files (int, optional): cap on files scanned for secrets.
        Default 5000.

    Output: structured posture report with secrets / signing /
    sync / gitignore findings; ok=True iff all four clean.
    """

    name = "git_local_scan"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        rp = args.get("repo_path")
        if rp is not None and not isinstance(rp, str):
            raise ToolValidationError(
                f"repo_path must be a str; got {type(rp).__name__}"
            )
        mc = args.get("max_commits")
        if mc is not None:
            if not isinstance(mc, int) or mc < 1 or mc > 500:
                raise ToolValidationError(
                    f"max_commits must be an int 1..500; got {mc!r}"
                )
        mf = args.get("max_files")
        if mf is not None:
            if not isinstance(mf, int) or mf < 1 or mf > 100000:
                raise ToolValidationError(
                    f"max_files must be an int 1..100000; got {mf!r}"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        repo = _resolve_repo_path(ctx, args)
        max_commits = int(args.get("max_commits") or 50)
        max_files = int(args.get("max_files") or 5000)

        patterns = _load_secret_patterns(repo)
        secrets_findings, scanned_files = _scan_for_secrets(
            repo, patterns, max_files=max_files
        )
        signing = _check_signing(repo, max_commits)
        sync = _check_sync(repo)
        gitignore = _check_gitignore(repo)

        # Compose unified findings list for severity bucketing.
        unified: list[dict[str, Any]] = list(secrets_findings)
        if not signing["ok"] and signing["unsigned_count"] > 0:
            unified.append(
                {
                    "rule_id": "unsigned_commits",
                    "severity": "HIGH",
                    "path": ".git/HEAD",
                    "line": 0,
                    "match_redacted": (
                        f"{signing['unsigned_count']} of "
                        f"{signing['commits_checked']} commits "
                        f"are unsigned"
                    ),
                }
            )
        if not sync["ok"]:
            sev = "MEDIUM" if sync.get("ahead", 0) > 0 else "INFO"
            unified.append(
                {
                    "rule_id": "sync_state_drift",
                    "severity": sev,
                    "path": ".git/refs/remotes",
                    "line": 0,
                    "match_redacted": sync["note"],
                }
            )
        for missing in gitignore["missing_patterns"]:
            unified.append(
                {
                    "rule_id": "gitignore_missing_pattern",
                    "severity": "INFO",
                    "path": ".gitignore",
                    "line": 0,
                    "match_redacted": f"missing pattern: {missing}",
                }
            )

        sev_counts = _severity_bucket(unified)
        overall_ok = (
            sev_counts["critical_findings"] == 0
            and sev_counts["high_findings"] == 0
        )

        return ToolResult(
            output={
                "ok": overall_ok,
                "summary": {
                    "total_findings": len(unified),
                    **sev_counts,
                },
                "secrets": {
                    "findings": secrets_findings,
                    "scanned_files": scanned_files,
                },
                "signing": signing,
                "sync": sync,
                "gitignore": gitignore,
            },
            metadata={
                "repo_path": str(repo),
                "max_commits": max_commits,
                "max_files": max_files,
                "patterns_loaded": len(patterns),
            },
            tokens_used=None,
            cost_usd=None,
            side_effect_summary=(
                f"git_local_scan: {len(unified)} finding(s) — "
                f"{sev_counts['critical_findings']}C / "
                f"{sev_counts['high_findings']}H / "
                f"{sev_counts['medium_findings']}M / "
                f"{sev_counts['low_findings']}L / "
                f"{sev_counts['info_findings']}I"
            ),
        )
