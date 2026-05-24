"""Autonomous self-improvement harness for Forest Soul Forge.

Runs a five-phase pipeline on its own:

    AUDIT  -> ANALYZE -> FIX -> VALIDATE -> REPORT

The harness creates a fresh git branch from current main, applies only
the fixes it can prove safe by re-running the affected tests, and
writes a structured markdown report under `docs/self-improvement/`.
The human reads the report and decides whether to merge — the script
never pushes or merges itself.

Usage:
    python scripts/self_improve.py
    python scripts/self_improve.py --audit-only
    python scripts/self_improve.py --no-branch

Exit codes:
    0  clean run (no fixes attempted or all fixes validated)
    1  regression detected (one or more applied fixes rolled back)
    2  audit-only mode (no fix phase executed)

Design notes:
- Stdlib + pyyaml only. The harness must not depend on FSF's own
  substrate imports, since one of its jobs is catching breakage in
  that substrate. pyyaml is the only outside dep and is already in
  the project's runtime requirements.
- Pure functions where possible. Each phase is a function that
  takes pure data and returns pure data. The thin main() does the
  I/O (subprocess, git, filesystem). Tests in tests/test_self_improve.py
  exercise the pure layer with synthetic fixtures.
- All timestamps America/Los_Angeles (Pacific) per project convention.
- Never use `git add -A`. The harness tracks the exact files it
  modifies and stages only those.
- The §0 Hippocratic gate from CLAUDE.md applies in spirit: the
  harness never removes anything. It only adds missing entries and
  corrects floor/ceiling violations. Anything more invasive gets
  flagged for human review.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

try:
    from zoneinfo import ZoneInfo
    _PT = ZoneInfo("America/Los_Angeles")
except Exception:  # pragma: no cover - zoneinfo is stdlib 3.9+
    _PT = None

import yaml  # type: ignore[import-untyped]


REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = REPO_ROOT / "docs" / "self-improvement"

# Files the harness considers "live" — modifications by external
# processes (daemon, scheduled tasks) are expected and the harness
# must never commit them on its own behalf.
LIVE_PATHS_IGNORE = {
    "examples/audit_chain.jsonl",
    "data/registry.sqlite",
}

# Severity ladder. CRITICAL = harness should refuse to run further
# work until human intervenes; HIGH = real bugs surfaced by failing
# tests; MEDIUM = drift between configs/code; LOW = lint/style.
SEVERITY_CRITICAL = "CRITICAL"
SEVERITY_HIGH = "HIGH"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_LOW = "LOW"
SEVERITY_ORDER = {
    SEVERITY_CRITICAL: 0,
    SEVERITY_HIGH: 1,
    SEVERITY_MEDIUM: 2,
    SEVERITY_LOW: 3,
}

# Complexity ladder. Only TRIVIAL and SIMPLE are auto-fixable.
COMPLEXITY_TRIVIAL = "TRIVIAL"
COMPLEXITY_SIMPLE = "SIMPLE"
COMPLEXITY_MODERATE = "MODERATE"
COMPLEXITY_COMPLEX = "COMPLEX"
AUTO_FIXABLE_COMPLEXITY = {COMPLEXITY_TRIVIAL, COMPLEXITY_SIMPLE}


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    """A single problem the harness detected.

    `kind` is the machine-readable classifier (e.g.
    "missing_role_in_genres") and drives Phase 2 routing.
    `details` carries the structured data the fixer needs (role,
    file, etc.).
    """
    kind: str
    severity: str
    summary: str
    details: dict = field(default_factory=dict)
    source: str = ""  # which check produced this

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FixOutcome:
    """Result of trying one fix.

    `status` is one of FIXED, REVERTED, SKIPPED, FLAGGED.
    `changed_files` is the list of paths the fix mutated (relative
    to repo root). `diff` is a human-readable summary of the change.
    """
    finding: Finding
    status: str
    changed_files: list[str] = field(default_factory=list)
    diff: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        out = asdict(self)
        out["finding"] = self.finding.to_dict()
        return out


@dataclass
class AuditResult:
    """Aggregated output of Phase 1."""
    findings: list[Finding] = field(default_factory=list)
    pytest_summary: dict = field(default_factory=dict)
    timestamp: str = ""

    def by_kind(self, kind: str) -> list[Finding]:
        return [f for f in self.findings if f.kind == kind]

    def by_severity(self, sev: str) -> list[Finding]:
        return [f for f in self.findings if f.severity == sev]


@dataclass
class FixPlan:
    """Output of Phase 2: ordered, classified, deduplicated."""
    auto_fix: list[Finding] = field(default_factory=list)
    flagged: list[Finding] = field(default_factory=list)
    grouping_notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Time + logging
# ---------------------------------------------------------------------------

def now_pt() -> datetime:
    """Pacific time, naive-safe fallback if zoneinfo missing."""
    if _PT is not None:
        return datetime.now(tz=_PT)
    return datetime.now()


def stamp_filename() -> str:
    """YYYY-MM-DD-HHMMSS suitable for branch + report filenames."""
    return now_pt().strftime("%Y-%m-%d-%H%M%S")


def stamp_log() -> str:
    """ISO-ish stamp with offset for stdout logging."""
    return now_pt().isoformat(timespec="seconds")


def log(msg: str) -> None:
    print(f"[{stamp_log()}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------

def run_cmd(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict | None = None,
    timeout: int = 600,
    check: bool = False,
) -> subprocess.CompletedProcess:
    """Run a subprocess and capture output. Wrapper around
    subprocess.run with sensible defaults (text mode, captured
    output, configurable timeout).
    """
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
    )


def pick_python(repo_root: Path) -> str:
    """Prefer the repo's .venv python if it exists — that's where
    pytest and project deps are installed. Fall back to the
    interpreter currently running this script.
    """
    candidate = repo_root / ".venv" / "bin" / "python"
    if candidate.exists():
        return str(candidate)
    return sys.executable


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def git_current_branch(repo_root: Path) -> str:
    cp = run_cmd(["git", "branch", "--show-current"], cwd=repo_root)
    return cp.stdout.strip()


def git_branch_exists(repo_root: Path, name: str) -> bool:
    cp = run_cmd(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{name}"],
        cwd=repo_root,
    )
    return cp.returncode == 0


def git_create_branch(repo_root: Path, name: str) -> None:
    """Create and switch to a new branch from the current HEAD.

    Unstaged changes ride along to the new branch by design — the
    harness never stashes or checks out tracked files, per the
    project memory: stashing the audit chain mid-run while the
    daemon is writing it could rewind live state.
    """
    cp = run_cmd(["git", "checkout", "-b", name], cwd=repo_root)
    if cp.returncode != 0:
        raise RuntimeError(f"git checkout -b {name} failed: {cp.stderr}")


def git_stage_files(repo_root: Path, paths: Iterable[str]) -> None:
    """Stage specific files. NEVER uses -A; the harness must not
    accidentally pick up the live audit chain or other untracked
    artifacts.
    """
    paths = [p for p in paths if p]
    if not paths:
        return
    cp = run_cmd(["git", "add", "--"] + list(paths), cwd=repo_root)
    if cp.returncode != 0:
        raise RuntimeError(f"git add failed: {cp.stderr}")


def git_commit(repo_root: Path, message: str) -> bool:
    """Returns True if a commit was created, False if nothing
    staged.
    """
    cp = run_cmd(
        ["git", "commit", "-m", message],
        cwd=repo_root,
    )
    if cp.returncode == 0:
        return True
    # "nothing to commit" is a clean exit, not an error.
    out = (cp.stdout + cp.stderr).lower()
    if "nothing to commit" in out or "nothing added" in out:
        return False
    raise RuntimeError(f"git commit failed: {cp.stderr}")


# ---------------------------------------------------------------------------
# Phase 1 — AUDIT
# ---------------------------------------------------------------------------

# Regex for pytest's final summary line. Pytest reorders the
# per-category counts based on which buckets are non-zero
# (`5345 passed` when all green, but `76 failed, 5345 passed`
# when red), so we extract the count for each keyword
# independently rather than baking order into the regex.
#
# Both formats need to be matched:
#   1. Bordered (verbose mode):
#        ==== 5 passed, 2 failed in 1.0s ====
#   2. Bare (with -q --no-header, which the harness uses):
#        5 passed, 2 failed in 1.0s
#
# Pytest also appends a wall-clock annotation like ` (0:02:59)`
# when the run takes longer than a minute, so we allow trailing
# text after the `<float>s`.
#
# We anchor on the trailing `in <float>s` since that's the most
# reliable marker — it's always present.
_PYTEST_SUMMARY_LINE_RE = re.compile(
    r"^(?:=+\s*)?(.*?\bin\s+[\d.]+\s*s)\b.*?$",
    re.MULTILINE,
)
_PYTEST_COUNT_KEYWORDS = (
    ("passed", "passed"),
    ("failed", "failed"),
    ("errors", "error"),    # both "error" and "errors"
    ("skipped", "skipped"),
    ("xfailed", "xfailed"),
    ("xpassed", "xpassed"),
    ("warnings", "warning"),  # both "warning" and "warnings"
)

# Matches a single failure line in -q --tb=line output:
#   FAILED tests/unit/test_foo.py::test_bar - AssertionError: ...
_PYTEST_FAILED_LINE_RE = re.compile(
    r"^(FAILED|ERROR)\s+(\S+)(?:\s*-\s*(.*))?$"
)


def parse_pytest_output(text: str) -> dict:
    """Parse the final summary line and per-failure lines from a
    pytest run. Returns a dict with keys:
      passed, failed, errors, skipped, xfailed, xpassed, warnings,
      failed_tests: [{"id": str, "error": str}, ...]

    Robust to multiple summary candidates (pytest sometimes prints
    a short summary then a final one); we pick the LAST match,
    which is the authoritative line.
    """
    summary = {
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "xfailed": 0,
        "xpassed": 0,
        "warnings": 0,
        "failed_tests": [],
    }
    # Find candidate summary lines (=== ... in N s ===). The last
    # one with any non-zero count is authoritative.
    candidates = _PYTEST_SUMMARY_LINE_RE.findall(text)
    chosen_line = ""
    for cand in reversed(candidates):
        if re.search(r"\d+\s+(passed|failed|error|skipped|xfailed|xpassed)", cand):
            chosen_line = cand
            break
    if chosen_line:
        for out_key, kw in _PYTEST_COUNT_KEYWORDS:
            # Match `N keyword` allowing the optional plural `s`.
            m = re.search(rf"(\d+)\s+{kw}s?\b", chosen_line)
            if m:
                summary[out_key] = int(m.group(1))

    for line in text.splitlines():
        line = line.strip()
        m = _PYTEST_FAILED_LINE_RE.match(line)
        if m:
            kind, test_id, err = m.group(1), m.group(2), m.group(3) or ""
            summary["failed_tests"].append({
                "id": test_id,
                "error": err.strip(),
                "kind": kind,  # FAILED vs ERROR
            })
    return summary


def run_pytest(
    repo_root: Path,
    *,
    timeout: int = 1800,
    extra_args: list[str] | None = None,
) -> dict:
    """Run the full test suite with FSF_SKIP_EMAIL_TESTS=1 and
    parseable output, return parsed summary + raw text. Returns
    `{"summary": dict, "raw": str, "returncode": int}`.
    """
    env = os.environ.copy()
    env.setdefault("FSF_SKIP_EMAIL_TESTS", "1")
    env.setdefault("PYTHONPATH", str(repo_root / "src"))
    py = pick_python(repo_root)
    cmd = [py, "-m", "pytest", "--tb=line", "--no-header", "-q"]
    if extra_args:
        cmd.extend(extra_args)
    cp = run_cmd(cmd, cwd=repo_root, env=env, timeout=timeout)
    summary = parse_pytest_output(cp.stdout + "\n" + cp.stderr)
    return {
        "summary": summary,
        "raw": cp.stdout + cp.stderr,
        "returncode": cp.returncode,
    }


def _load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def check_config_drift(repo_root: Path) -> list[Finding]:
    """Cross-reference roles across trait_tree.yaml, genres.yaml,
    constitution_templates.yaml. Tool catalog uses archetype tags
    (not roles directly) so it's checked separately.

    A role missing from genres OR constitution_templates produces
    one Finding per (role, file) pair. Genres carrying an
    aspirational role (in genres but not in trait_tree) is allowed
    and not a Finding — the loader explicitly permits it.
    """
    findings: list[Finding] = []
    cfg = repo_root / "config"
    try:
        tt = _load_yaml(cfg / "trait_tree.yaml") or {}
        gn = _load_yaml(cfg / "genres.yaml") or {}
        ct = _load_yaml(cfg / "constitution_templates.yaml") or {}
    except yaml.YAMLError as e:
        findings.append(Finding(
            kind="yaml_parse_error",
            severity=SEVERITY_CRITICAL,
            summary=f"YAML parse error in config/: {e}",
            details={"error": str(e)},
            source="check_config_drift",
        ))
        return findings

    tt_roles = set((tt.get("roles") or {}).keys())
    ct_roles = set((ct.get("role_base") or {}).keys())
    gn_roles: set[str] = set()
    for genre, gdef in (gn.get("genres") or {}).items():
        for r in gdef.get("roles", []) or []:
            gn_roles.add(r)

    for role in sorted(tt_roles - gn_roles):
        findings.append(Finding(
            kind="missing_role_in_genres",
            severity=SEVERITY_MEDIUM,
            summary=f"Role {role!r} is in trait_tree but no genre claims it",
            details={"role": role},
            source="check_config_drift",
        ))
    for role in sorted(tt_roles - ct_roles):
        findings.append(Finding(
            kind="missing_role_in_constitution",
            severity=SEVERITY_MEDIUM,
            summary=f"Role {role!r} is in trait_tree but has no constitution template",
            details={"role": role},
            source="check_config_drift",
        ))
    # trait floor/ceiling sanity. Per the v0.2 schema, every
    # domain_weight value should be > 0. Zero or negative blocks
    # birth. We also surface the embodiment-floor convention
    # (>= 0.4 per the ADR-0067 D6 lesson, since the kernel
    # rejects sub-floor values).
    floors = {
        "embodiment": 0.4,
    }
    for role, rdef in (tt.get("roles") or {}).items():
        for dom, val in (rdef.get("domain_weights") or {}).items():
            try:
                v = float(val)
            except (TypeError, ValueError):
                findings.append(Finding(
                    kind="trait_value_invalid",
                    severity=SEVERITY_HIGH,
                    summary=f"Non-numeric domain_weight {role}.{dom}={val!r}",
                    details={"role": role, "domain": dom, "value": val},
                    source="check_config_drift",
                ))
                continue
            if v <= 0:
                findings.append(Finding(
                    kind="trait_value_invalid",
                    severity=SEVERITY_HIGH,
                    summary=f"Non-positive domain_weight {role}.{dom}={v}",
                    details={"role": role, "domain": dom, "value": v},
                    source="check_config_drift",
                ))
            floor = floors.get(dom)
            if floor is not None and v < floor:
                findings.append(Finding(
                    kind="trait_floor_violation",
                    severity=SEVERITY_HIGH,
                    summary=(
                        f"{role}.{dom}={v} below floor {floor} "
                        f"(kernel rejects sub-floor at birth)"
                    ),
                    details={
                        "role": role,
                        "domain": dom,
                        "value": v,
                        "floor": floor,
                    },
                    source="check_config_drift",
                ))
    return findings


def check_tool_registration(repo_root: Path) -> list[Finding]:
    """Verify every tool in tool_catalog has a corresponding
    Python module in tools/builtin/. The catalog key
    `<name>.v<version>` should resolve to a file
    `tools/builtin/<name>.py` containing a class with matching
    `name` and `version`.

    Bare-version-string drift (CLAUDE.md §3) is also checked here:
    the `version` attribute must be `"1"` not `"v1"`.
    """
    findings: list[Finding] = []
    catalog_path = repo_root / "config" / "tool_catalog.yaml"
    builtin_dir = repo_root / "src" / "forest_soul_forge" / "tools" / "builtin"
    if not catalog_path.exists():
        findings.append(Finding(
            kind="catalog_missing",
            severity=SEVERITY_CRITICAL,
            summary="config/tool_catalog.yaml not found",
            source="check_tool_registration",
        ))
        return findings
    try:
        catalog = _load_yaml(catalog_path) or {}
    except yaml.YAMLError as e:
        findings.append(Finding(
            kind="yaml_parse_error",
            severity=SEVERITY_CRITICAL,
            summary=f"tool_catalog.yaml parse error: {e}",
            source="check_tool_registration",
        ))
        return findings
    tools = catalog.get("tools") or {}
    for key, tdef in tools.items():
        # Expected key shape: <name>.v<version>
        m = re.match(r"^([a-z_][a-z0-9_]*)\.v(\d+)$", key)
        if not m:
            findings.append(Finding(
                kind="catalog_key_malformed",
                severity=SEVERITY_HIGH,
                summary=f"Catalog key {key!r} not in form name.v<digits>",
                details={"key": key},
                source="check_tool_registration",
            ))
            continue
        name, ver = m.group(1), m.group(2)
        module = builtin_dir / f"{name}.py"
        if not module.exists():
            findings.append(Finding(
                kind="tool_module_missing",
                severity=SEVERITY_HIGH,
                summary=f"Catalog tool {key!r} has no module {module.name}",
                details={
                    "tool": key,
                    "expected_module": str(module.relative_to(repo_root)),
                },
                source="check_tool_registration",
            ))
            continue
        # Quick string scan for `_VERSION = "v...` — flag if bare
        # version-string convention is violated.
        try:
            text = module.read_text(encoding="utf-8")
        except OSError:
            continue
        bad_ver = re.search(r"""_VERSION\s*=\s*['"]v\d""", text)
        if bad_ver:
            findings.append(Finding(
                kind="version_string_prefixed",
                severity=SEVERITY_HIGH,
                summary=(
                    f"{name}.py uses v-prefixed _VERSION; should be "
                    f'bare numeric ("1") per CLAUDE.md §3'
                ),
                details={"module": str(module.relative_to(repo_root))},
                source="check_tool_registration",
            ))
    return findings


def check_skill_manifests(repo_root: Path) -> list[Finding]:
    """Walk examples/skills/*.yaml and verify:
      - YAML parses
      - schema_version present
      - referenced tools (in `requires:` and step `tool:` fields)
        exist in tool_catalog
    """
    findings: list[Finding] = []
    skills_dir = repo_root / "examples" / "skills"
    catalog_path = repo_root / "config" / "tool_catalog.yaml"
    catalog_keys: set[str] = set()
    if catalog_path.exists():
        try:
            catalog = _load_yaml(catalog_path) or {}
            catalog_keys = set((catalog.get("tools") or {}).keys())
        except yaml.YAMLError:
            pass
    if not skills_dir.exists():
        return findings
    for path in sorted(skills_dir.glob("*.yaml")):
        rel = path.relative_to(repo_root)
        try:
            d = _load_yaml(path)
        except yaml.YAMLError as e:
            findings.append(Finding(
                kind="skill_yaml_parse_error",
                severity=SEVERITY_HIGH,
                summary=f"Skill manifest {rel} fails to parse",
                details={"path": str(rel), "error": str(e)},
                source="check_skill_manifests",
            ))
            continue
        if not isinstance(d, dict):
            findings.append(Finding(
                kind="skill_yaml_not_dict",
                severity=SEVERITY_HIGH,
                summary=f"Skill manifest {rel} is not a mapping",
                details={"path": str(rel)},
                source="check_skill_manifests",
            ))
            continue
        if "schema_version" not in d:
            findings.append(Finding(
                kind="skill_missing_schema_version",
                severity=SEVERITY_MEDIUM,
                summary=f"Skill manifest {rel} missing schema_version",
                details={"path": str(rel)},
                source="check_skill_manifests",
            ))
        # Aggregate referenced tools.
        refs: set[str] = set()
        for r in (d.get("requires") or []):
            if isinstance(r, str):
                refs.add(r)
        for step in (d.get("steps") or []):
            if isinstance(step, dict) and isinstance(step.get("tool"), str):
                refs.add(step["tool"])
        if catalog_keys:  # only check refs if we managed to load catalog
            for ref in sorted(refs - catalog_keys):
                findings.append(Finding(
                    kind="skill_unknown_tool_ref",
                    severity=SEVERITY_HIGH,
                    summary=(
                        f"Skill {rel.name} references unknown tool {ref!r}"
                    ),
                    details={"path": str(rel), "tool": ref},
                    source="check_skill_manifests",
                ))
    return findings


def check_syntax_errors(repo_root: Path) -> list[Finding]:
    """Walk every .py under src/ and compile-check it. Anything
    that fails to parse is a CRITICAL finding — the substrate
    can't import broken code.
    """
    findings: list[Finding] = []
    src = repo_root / "src"
    if not src.exists():
        return findings
    for py in src.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        try:
            ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError as e:
            findings.append(Finding(
                kind="syntax_error",
                severity=SEVERITY_CRITICAL,
                summary=f"SyntaxError in {py.relative_to(repo_root)}: {e.msg}",
                details={
                    "path": str(py.relative_to(repo_root)),
                    "line": e.lineno,
                    "msg": e.msg,
                },
                source="check_syntax_errors",
            ))
        except (OSError, UnicodeDecodeError) as e:
            findings.append(Finding(
                kind="file_read_error",
                severity=SEVERITY_MEDIUM,
                summary=f"Could not read {py.relative_to(repo_root)}: {e}",
                details={"path": str(py.relative_to(repo_root))},
                source="check_syntax_errors",
            ))
    return findings


def run_ruff(repo_root: Path) -> list[Finding]:
    """Run ruff if available. Anything it surfaces is LOW
    severity (we don't auto-fix lint). Gracefully degrades when
    ruff isn't on the path.
    """
    findings: list[Finding] = []
    ruff = shutil.which("ruff")
    if ruff is None:
        return findings
    cp = run_cmd(
        [ruff, "check", "--output-format=json", "src", "scripts", "tests"],
        cwd=repo_root,
        timeout=120,
    )
    if not cp.stdout.strip():
        return findings
    try:
        items = json.loads(cp.stdout)
    except json.JSONDecodeError:
        return findings
    for item in items[:200]:  # cap to keep reports bounded
        findings.append(Finding(
            kind="lint",
            severity=SEVERITY_LOW,
            summary=(
                f"{item.get('code', '?')} {item.get('filename', '?')}:"
                f"{(item.get('location') or {}).get('row', '?')} — "
                f"{item.get('message', '?')}"
            ),
            details=item,
            source="run_ruff",
        ))
    return findings


def phase1_audit(repo_root: Path, *, skip_pytest: bool = False) -> AuditResult:
    """Orchestrate Phase 1. Returns AuditResult with all findings
    + pytest summary.
    """
    log("Phase 1 — AUDIT")
    findings: list[Finding] = []

    log("  -> config drift")
    findings.extend(check_config_drift(repo_root))
    log("  -> tool registration")
    findings.extend(check_tool_registration(repo_root))
    log("  -> skill manifests")
    findings.extend(check_skill_manifests(repo_root))
    log("  -> syntax errors")
    findings.extend(check_syntax_errors(repo_root))
    log("  -> ruff (if available)")
    findings.extend(run_ruff(repo_root))

    pytest_summary: dict = {}
    if not skip_pytest:
        log("  -> pytest (full suite)")
        result = run_pytest(repo_root)
        pytest_summary = result["summary"]
        log(
            f"     pytest: passed={pytest_summary['passed']} "
            f"failed={pytest_summary['failed']} "
            f"errors={pytest_summary['errors']} "
            f"skipped={pytest_summary['skipped']}"
        )
        # Each pytest failure becomes its own Finding so Phase 2
        # can route it. We classify it HIGH by default; the
        # severity is downgraded only when grouping in Phase 2.
        for ft in pytest_summary.get("failed_tests", []):
            findings.append(Finding(
                kind="test_failure",
                severity=SEVERITY_HIGH,
                summary=f"{ft['kind']} {ft['id']}",
                details=ft,
                source="run_pytest",
            ))

    return AuditResult(
        findings=findings,
        pytest_summary=pytest_summary,
        timestamp=stamp_log(),
    )


# ---------------------------------------------------------------------------
# Phase 2 — ANALYZE
# ---------------------------------------------------------------------------

# Mapping from finding.kind -> (default complexity, auto-fix supported)
COMPLEXITY_MAP: dict[str, str] = {
    "missing_role_in_genres":      COMPLEXITY_SIMPLE,
    "missing_role_in_constitution": COMPLEXITY_SIMPLE,
    "trait_floor_violation":       COMPLEXITY_TRIVIAL,
    "trait_value_invalid":         COMPLEXITY_MODERATE,
    "skill_missing_schema_version": COMPLEXITY_TRIVIAL,
    "skill_yaml_parse_error":      COMPLEXITY_MODERATE,
    "skill_yaml_not_dict":         COMPLEXITY_MODERATE,
    "skill_unknown_tool_ref":      COMPLEXITY_MODERATE,
    "tool_module_missing":         COMPLEXITY_MODERATE,
    "version_string_prefixed":     COMPLEXITY_TRIVIAL,
    "catalog_key_malformed":       COMPLEXITY_MODERATE,
    "syntax_error":                COMPLEXITY_COMPLEX,
    "test_failure":                COMPLEXITY_COMPLEX,
    "yaml_parse_error":            COMPLEXITY_COMPLEX,
    "lint":                        COMPLEXITY_TRIVIAL,
    "file_read_error":             COMPLEXITY_COMPLEX,
    "catalog_missing":             COMPLEXITY_COMPLEX,
}


def classify_complexity(finding: Finding) -> str:
    """Look up the kind's complexity; default to COMPLEX (flagged
    for human review) when unknown — fail-safe.
    """
    return COMPLEXITY_MAP.get(finding.kind, COMPLEXITY_COMPLEX)


def classify_severity(finding: Finding) -> str:
    """Findings carry their own severity from Phase 1. This hook
    exists for downstream override if grouping reveals a higher
    severity (e.g. 50 test failures tracing to one root cause is
    HIGH, not 50 HIGHs).
    """
    return finding.severity


def group_findings(findings: list[Finding]) -> tuple[list[Finding], list[str]]:
    """Collapse trivially-grouped findings. Today this groups
    test_failure findings by test-module prefix so the report
    doesn't list 50 lines for one root cause.

    Returns (grouped_findings, notes_for_report).
    """
    notes: list[str] = []
    test_failures = [f for f in findings if f.kind == "test_failure"]
    others = [f for f in findings if f.kind != "test_failure"]

    if len(test_failures) <= 3:
        # Don't bother grouping small failure sets.
        return findings, notes

    # Group by module path (everything before the first ::).
    by_module: dict[str, list[Finding]] = {}
    for f in test_failures:
        tid = f.details.get("id", "") or ""
        mod = tid.split("::", 1)[0] if "::" in tid else tid
        by_module.setdefault(mod, []).append(f)

    grouped: list[Finding] = []
    for mod, group in sorted(by_module.items()):
        if len(group) == 1:
            grouped.extend(group)
            continue
        # Build a synthetic grouped finding.
        first_err = group[0].details.get("error", "")
        grouped.append(Finding(
            kind="test_failure_group",
            severity=SEVERITY_HIGH,
            summary=f"{len(group)} failures in {mod}",
            details={
                "module": mod,
                "count": len(group),
                "tests": [f.details.get("id") for f in group],
                "sample_error": first_err,
            },
            source="group_findings",
        ))
        notes.append(
            f"Grouped {len(group)} test failures in {mod} "
            f"into one root cause"
        )
    return others + grouped, notes


def phase2_analyze(audit: AuditResult) -> FixPlan:
    """Partition findings into auto-fix vs flagged, by complexity.

    Complexity TRIVIAL or SIMPLE -> auto-fix queue (Phase 3 will
    still verify each one and revert on failure).
    Anything else -> flagged for human review.
    """
    log("Phase 2 — ANALYZE")
    findings, notes = group_findings(audit.findings)

    auto: list[Finding] = []
    flagged: list[Finding] = []
    for f in findings:
        cplx = classify_complexity(f)
        if cplx in AUTO_FIXABLE_COMPLEXITY:
            auto.append(f)
        else:
            flagged.append(f)

    # Sort by severity then kind for stable reporting.
    auto.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.kind))
    flagged.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.kind))
    log(f"  auto-fix queue: {len(auto)}  flagged for human: {len(flagged)}")
    return FixPlan(auto_fix=auto, flagged=flagged, grouping_notes=notes)


# ---------------------------------------------------------------------------
# Phase 3 — FIX
# ---------------------------------------------------------------------------

# Default genre to add a role to when we have no better signal.
# "specialist" is the catch-all genre per genres.yaml (action-taker
# at investigator side-effects ceiling). We choose this over
# observer/companion because most aspirational-but-missing roles
# end up doing some action.
DEFAULT_GENRE_FOR_NEW_ROLE = "specialist"


def fix_missing_role_in_genres(
    repo_root: Path, role: str,
) -> tuple[bool, str, list[str]]:
    """Append `role` to the DEFAULT_GENRE_FOR_NEW_ROLE roles list
    in genres.yaml. Returns (success, diff_summary, changed_files).

    Done with a string edit rather than yaml.dump so we preserve
    the file's anchors, comments, and ordering. We locate the
    target genre and insert the role at the end of its `roles:`
    block, matching the indentation of existing entries.
    """
    path = repo_root / "config" / "genres.yaml"
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Find the `  <genre>:` header.
    header_re = re.compile(
        rf"^\s{{2}}{re.escape(DEFAULT_GENRE_FOR_NEW_ROLE)}:\s*$"
    )
    roles_re = re.compile(r"^\s{4}roles:\s*$")
    item_re = re.compile(r"^\s{6}-\s+\S")
    header_idx = next(
        (i for i, l in enumerate(lines) if header_re.match(l)),
        -1,
    )
    if header_idx < 0:
        return False, (
            f"target genre {DEFAULT_GENRE_FOR_NEW_ROLE!r} not "
            f"found in genres.yaml"
        ), []
    # Find roles: under it.
    roles_idx = -1
    for j in range(header_idx + 1, len(lines)):
        # Stop if we hit the next top-level genre header.
        if re.match(r"^\s{2}\S", lines[j]) and j != header_idx:
            break
        if roles_re.match(lines[j]):
            roles_idx = j
            break
    if roles_idx < 0:
        return False, f"genre {DEFAULT_GENRE_FOR_NEW_ROLE!r} has no roles: block", []
    # Find the last `- role` item in that block.
    last_item = roles_idx
    for k in range(roles_idx + 1, len(lines)):
        if item_re.match(lines[k]):
            last_item = k
        elif lines[k].strip() == "":
            continue
        else:
            break
    insert_at = last_item + 1
    new_line = f"      - {role}"
    lines.insert(insert_at, new_line)
    new_text = "\n".join(lines)
    if not text.endswith("\n"):
        # Preserve absence of trailing newline if any.
        path.write_text(new_text, encoding="utf-8")
    else:
        path.write_text(new_text + "\n", encoding="utf-8")
    rel = str(path.relative_to(repo_root))
    return True, (
        f"genres.yaml: added {role!r} under "
        f"{DEFAULT_GENRE_FOR_NEW_ROLE!r} (line {insert_at + 1})"
    ), [rel]


# Minimal constitution stub that satisfies the schema while
# being a transparent placeholder. The point is to clear the
# drift; the human reviewer fills in real policies post-merge.
_MINIMAL_CONSTITUTION_STUB = """  {role}:
    policies:
      - id: placeholder_human_review_required
        rule: require_human_approval
        triggers: [any_action]
        rationale: "Auto-generated stub. Replace with real policies before this role is birthed in production."
    risk_thresholds:
      side_effect_default: read_only
    out_of_scope:
      - "Any action not explicitly authorized by the operator."
    operator_duties:
      - "Operator must define real policies before deploying this role."
    drift_monitoring:
      check_at_birth: true
"""


def fix_missing_role_in_constitution(
    repo_root: Path, role: str,
) -> tuple[bool, str, list[str]]:
    """Insert a placeholder constitution template at the end of
    the role_base: block.

    The stub forbids all autonomous action (rule:
    require_human_approval with trigger any_action). It exists
    only to unblock birth-time validation and make the gap visible
    in code review.
    """
    path = repo_root / "config" / "constitution_templates.yaml"
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    # Find `role_base:` line.
    rb_idx = next((i for i, l in enumerate(lines) if l.rstrip() == "role_base:"), -1)
    if rb_idx < 0:
        return False, "constitution_templates.yaml has no role_base:", []
    # Find end of role_base block: next non-indented non-empty line.
    end_idx = len(lines)
    for j in range(rb_idx + 1, len(lines)):
        if lines[j] and not lines[j].startswith(" "):
            end_idx = j
            break
    stub_block = _MINIMAL_CONSTITUTION_STUB.format(role=role).rstrip("\n")
    insert_at = end_idx
    # Insert with a leading blank line for readability.
    new_lines = lines[:insert_at] + ["", stub_block] + lines[insert_at:]
    path.write_text(
        "\n".join(new_lines) + ("\n" if text.endswith("\n") else ""),
        encoding="utf-8",
    )
    rel = str(path.relative_to(repo_root))
    return True, (
        f"constitution_templates.yaml: appended placeholder "
        f"stub for {role!r}"
    ), [rel]


def fix_trait_floor_violation(
    repo_root: Path, role: str, domain: str, floor: float,
) -> tuple[bool, str, list[str]]:
    """Bump the violating trait value up to `floor`.

    Edit is line-scoped: locate the `<role>:` header, then within
    its domain_weights block locate the `  <domain>:` line and
    replace the numeric value. We do NOT touch surrounding lines
    or comments.
    """
    path = repo_root / "config" / "trait_tree.yaml"
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    role_header_re = re.compile(rf"^\s{{2}}{re.escape(role)}:\s*$")
    dw_re = re.compile(r"^\s{4}domain_weights:\s*$")
    dom_re = re.compile(
        rf"^(\s{{6}}{re.escape(domain)}):\s*([\d.]+)\s*(#.*)?$"
    )
    header_idx = next(
        (i for i, l in enumerate(lines) if role_header_re.match(l)), -1
    )
    if header_idx < 0:
        return False, f"role {role!r} not found in trait_tree.yaml", []
    # Find domain_weights block.
    dw_idx = -1
    for j in range(header_idx + 1, len(lines)):
        if re.match(r"^\s{2}\S", lines[j]):
            break
        if dw_re.match(lines[j]):
            dw_idx = j
            break
    if dw_idx < 0:
        return False, f"role {role!r} has no domain_weights:", []
    # Find the domain line.
    for k in range(dw_idx + 1, len(lines)):
        if re.match(r"^\s{0,4}\S", lines[k]):
            break
        m = dom_re.match(lines[k])
        if m:
            comment = m.group(3) or ""
            new_line = f"{m.group(1)}: {floor}"
            if comment:
                new_line = f"{new_line}  {comment}"
            old_val = m.group(2)
            lines[k] = new_line
            path.write_text(
                "\n".join(lines) + ("\n" if text.endswith("\n") else ""),
                encoding="utf-8",
            )
            return True, (
                f"trait_tree.yaml: {role}.{domain} {old_val} -> "
                f"{floor} (floor enforced)"
            ), [str(path.relative_to(repo_root))]
    return False, f"domain {domain!r} not found under role {role!r}", []


def fix_skill_missing_schema_version(
    repo_root: Path, skill_path: str,
) -> tuple[bool, str, list[str]]:
    """Prepend `schema_version: 1` to a skill manifest that's
    missing the field. We insert it as the first non-comment line
    so the rest of the file is untouched.
    """
    path = repo_root / skill_path
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    # Find first non-comment, non-blank line.
    insert_at = 0
    for i, l in enumerate(lines):
        s = l.strip()
        if s and not s.startswith("#"):
            insert_at = i
            break
    lines.insert(insert_at, "schema_version: 1")
    path.write_text(
        "\n".join(lines) + ("\n" if text.endswith("\n") else ""),
        encoding="utf-8",
    )
    return True, f"{skill_path}: added schema_version: 1", [skill_path]


def fix_version_string_prefixed(
    repo_root: Path, module_path: str,
) -> tuple[bool, str, list[str]]:
    """Strip a `v` from `_VERSION = "v<digits>"` -> `_VERSION = "<digits>"`.

    Single-line edit, deterministic regex.
    """
    path = repo_root / module_path
    text = path.read_text(encoding="utf-8")
    new_text, n = re.subn(
        r"""(_VERSION\s*=\s*['"])v(\d+)(['"])""",
        r"\1\2\3",
        text,
    )
    if n == 0:
        return False, "no v-prefixed _VERSION found to fix", []
    path.write_text(new_text, encoding="utf-8")
    return True, (
        f"{module_path}: stripped v-prefix from _VERSION "
        f"({n} replacement)"
    ), [module_path]


def apply_fix(repo_root: Path, finding: Finding) -> FixOutcome:
    """Dispatch to the per-kind fixer. Returns FixOutcome.

    On exception, returns SKIPPED with the error captured — never
    raises, so one bad finding can't tank the whole run.
    """
    try:
        if finding.kind == "missing_role_in_genres":
            ok, msg, files = fix_missing_role_in_genres(
                repo_root, finding.details["role"]
            )
        elif finding.kind == "missing_role_in_constitution":
            ok, msg, files = fix_missing_role_in_constitution(
                repo_root, finding.details["role"]
            )
        elif finding.kind == "trait_floor_violation":
            ok, msg, files = fix_trait_floor_violation(
                repo_root,
                finding.details["role"],
                finding.details["domain"],
                float(finding.details["floor"]),
            )
        elif finding.kind == "skill_missing_schema_version":
            ok, msg, files = fix_skill_missing_schema_version(
                repo_root, finding.details["path"]
            )
        elif finding.kind == "version_string_prefixed":
            ok, msg, files = fix_version_string_prefixed(
                repo_root, finding.details["module"]
            )
        else:
            return FixOutcome(
                finding=finding,
                status="SKIPPED",
                error=f"no fixer for kind {finding.kind!r}",
            )
        if not ok:
            return FixOutcome(
                finding=finding,
                status="SKIPPED",
                error=msg,
            )
        return FixOutcome(
            finding=finding,
            status="FIXED",
            changed_files=files,
            diff=msg,
        )
    except Exception as e:  # defensive: never crash the harness
        return FixOutcome(
            finding=finding,
            status="SKIPPED",
            error=f"{type(e).__name__}: {e}",
        )


def verify_fix_with_tests(
    repo_root: Path,
    outcome: FixOutcome,
    *,
    timeout: int = 300,
) -> bool:
    """Run a targeted subset of the suite after a fix. Returns
    True if no new failures surfaced.

    The targeted subset for config-drift fixes is the config-loader
    test files (tests/unit/test_*_config*.py + test_genres*.py +
    test_constitution*.py + test_trait*.py). We don't re-run the
    full suite per fix — that's Phase 4's job.
    """
    targets = [
        "tests/unit/test_genres_loader.py",
        "tests/unit/test_constitution_templates_load.py",
        "tests/unit/test_trait_engine.py",
        "tests/unit/test_tool_catalog.py",
    ]
    # Filter to files that actually exist.
    targets = [t for t in targets if (repo_root / t).exists()]
    if not targets:
        # Nothing specific to run; treat as passing.
        return True
    env = os.environ.copy()
    env.setdefault("FSF_SKIP_EMAIL_TESTS", "1")
    env.setdefault("PYTHONPATH", str(repo_root / "src"))
    py = pick_python(repo_root)
    cp = run_cmd(
        [py, "-m", "pytest", "--tb=line", "--no-header", "-q"] + targets,
        cwd=repo_root,
        env=env,
        timeout=timeout,
    )
    return cp.returncode == 0


def revert_fix(repo_root: Path, outcome: FixOutcome) -> None:
    """Restore the files this fix touched from HEAD. Used both
    when the per-fix verification fails and in Phase 4 when a
    regression is traced to a specific fix.
    """
    if not outcome.changed_files:
        return
    cp = run_cmd(
        ["git", "checkout", "HEAD", "--"] + outcome.changed_files,
        cwd=repo_root,
    )
    if cp.returncode != 0:
        log(f"  WARN: revert of {outcome.changed_files} failed: {cp.stderr}")


def phase3_fix(repo_root: Path, plan: FixPlan) -> list[FixOutcome]:
    """Apply each fix; verify it; revert if it breaks something."""
    log("Phase 3 — FIX")
    outcomes: list[FixOutcome] = []
    for f in plan.auto_fix:
        log(f"  fix: {f.kind} ({f.summary})")
        outcome = apply_fix(repo_root, f)
        if outcome.status == "FIXED":
            ok = verify_fix_with_tests(repo_root, outcome)
            if not ok:
                log(f"    -> targeted tests failed; reverting")
                revert_fix(repo_root, outcome)
                outcome.status = "REVERTED"
                outcome.error = "targeted tests failed after fix"
        outcomes.append(outcome)
    return outcomes


# ---------------------------------------------------------------------------
# Phase 4 — VALIDATE
# ---------------------------------------------------------------------------

def compute_regression(
    before: dict, after: dict,
) -> dict:
    """Compare two pytest summaries. Returns a dict with:
      fixed_tests:    [ids that were failing before, passing after]
      broken_tests:   [ids passing before, failing after] (regressions)
      still_failing:  [ids failing in both]
      delta:          {passed_delta, failed_delta, errors_delta}
    """
    before_failed = {t["id"] for t in before.get("failed_tests", [])}
    after_failed = {t["id"] for t in after.get("failed_tests", [])}
    return {
        "fixed_tests": sorted(before_failed - after_failed),
        "broken_tests": sorted(after_failed - before_failed),
        "still_failing": sorted(before_failed & after_failed),
        "delta": {
            "passed_delta": after.get("passed", 0) - before.get("passed", 0),
            "failed_delta": after.get("failed", 0) - before.get("failed", 0),
            "errors_delta": after.get("errors", 0) - before.get("errors", 0),
        },
    }


def phase4_validate(
    repo_root: Path,
    before: dict,
    outcomes: list[FixOutcome],
) -> dict:
    """Re-run the full suite, compute before/after deltas, and
    revert any specific fix that introduced a regression.
    """
    log("Phase 4 — VALIDATE")
    log("  -> re-running full pytest suite")
    result = run_pytest(repo_root)
    after = result["summary"]
    log(
        f"     pytest after: passed={after['passed']} "
        f"failed={after['failed']} errors={after['errors']} "
        f"skipped={after['skipped']}"
    )
    diff = compute_regression(before, after)
    diff["before"] = before
    diff["after"] = after

    if diff["broken_tests"]:
        log(f"  REGRESSION DETECTED: {len(diff['broken_tests'])} newly failing tests")
        # Attribution heuristic: any FIXED outcome that mentions
        # the same module-prefix as a broken test is suspect.
        # In the absence of better info, revert ALL applied fixes.
        # This is the safe default — the human can then re-apply
        # them one at a time.
        for o in outcomes:
            if o.status == "FIXED":
                log(f"    reverting fix: {o.diff}")
                revert_fix(repo_root, o)
                o.status = "REVERTED"
                o.error = "rolled back due to phase-4 regression"
        # Re-run after revert to confirm we're back to the
        # pre-fix baseline.
        log("  -> re-running pytest after full revert")
        recheck = run_pytest(repo_root)
        diff["after_revert"] = recheck["summary"]

    # Re-check config consistency to make sure fixes didn't
    # create new drift.
    log("  -> re-running config drift check")
    diff["post_fix_config_findings"] = [
        f.to_dict() for f in check_config_drift(repo_root)
    ]
    return diff


# ---------------------------------------------------------------------------
# Phase 5 — REPORT
# ---------------------------------------------------------------------------

def _render_finding_row(f: Finding, status: str) -> str:
    """One markdown table row per finding."""
    return (
        f"| {f.severity} | {status} | `{f.kind}` | "
        f"{f.summary.replace('|', '\\|')} |"
    )


def _render_outcome_row(o: FixOutcome) -> str:
    return (
        f"| {o.finding.severity} | {o.status} | `{o.finding.kind}` | "
        f"{o.diff.replace('|', '\\|') or o.error.replace('|', '\\|') or o.finding.summary.replace('|', '\\|')} |"
    )


def render_report(
    *,
    branch_name: str,
    audit: AuditResult,
    plan: FixPlan,
    outcomes: list[FixOutcome],
    validation: dict,
    audit_only: bool,
) -> str:
    """Compose the markdown report. All inputs are pure data so
    this function is unit-testable without filesystem state.
    """
    when = audit.timestamp or stamp_log()
    fixed = [o for o in outcomes if o.status == "FIXED"]
    reverted = [o for o in outcomes if o.status == "REVERTED"]
    skipped = [o for o in outcomes if o.status == "SKIPPED"]
    flagged = plan.flagged

    lines: list[str] = []
    lines.append(f"# Self-Improvement Report — {when}")
    lines.append("")
    lines.append("## Executive summary")
    lines.append("")
    lines.append(f"- Branch: `{branch_name}`")
    raw_count = len(audit.findings)
    grouped_count = len(plan.auto_fix) + len(plan.flagged)
    if grouped_count != raw_count:
        lines.append(
            f"- Findings detected: **{raw_count}** raw → "
            f"**{grouped_count}** after grouping"
        )
    else:
        lines.append(f"- Findings detected: **{raw_count}**")
    lines.append(f"- Auto-fixes applied: **{len(fixed)}**")
    lines.append(f"- Auto-fixes reverted: **{len(reverted)}**")
    lines.append(f"- Auto-fixes skipped: **{len(skipped)}**")
    lines.append(f"- Flagged for human review: **{len(flagged)}**")
    if audit_only:
        lines.append("- Mode: **AUDIT-ONLY** (no fix phase executed)")
    lines.append("")

    # Test count delta
    before = validation.get("before") if validation else None
    after = validation.get("after") if validation else None
    if before is not None and after is not None:
        lines.append("## Test counts — before vs. after")
        lines.append("")
        lines.append("| Metric | Before | After | Delta |")
        lines.append("|---|---:|---:|---:|")
        for k in ("passed", "failed", "errors", "skipped"):
            b = before.get(k, 0)
            a = after.get(k, 0)
            d = a - b
            lines.append(f"| {k} | {b} | {a} | {d:+d} |")
        lines.append("")
        if validation.get("fixed_tests"):
            lines.append("**Tests fixed by the harness:**")
            for t in validation["fixed_tests"][:50]:
                lines.append(f"- `{t}`")
            lines.append("")
        if validation.get("broken_tests"):
            lines.append("**Regressions (rolled back):**")
            for t in validation["broken_tests"][:50]:
                lines.append(f"- `{t}`")
            lines.append("")
    elif audit.pytest_summary:
        lines.append("## Test counts")
        lines.append("")
        s = audit.pytest_summary
        lines.append(f"- passed: {s.get('passed', 0)}")
        lines.append(f"- failed: {s.get('failed', 0)}")
        lines.append(f"- errors: {s.get('errors', 0)}")
        lines.append(f"- skipped: {s.get('skipped', 0)}")
        lines.append("")

    # All findings table. We render the POST-grouping view
    # (plan.auto_fix + plan.flagged) — the originals in
    # audit.findings may have been collapsed into a single
    # test_failure_group, and showing both would double-count.
    # When test_failure_group rows are present, the table also
    # includes the rolled-up test count in the Kind column.
    lines.append("## All findings")
    lines.append("")
    all_findings = list(plan.auto_fix) + list(plan.flagged)
    if not all_findings:
        lines.append("_No findings. The harness has nothing to do._")
    else:
        lines.append("| Severity | Status | Kind | Summary |")
        lines.append("|---|---|---|---|")
        outcome_by_id: dict[int, FixOutcome] = {
            id(o.finding): o for o in outcomes
        }
        flagged_set = {id(f) for f in plan.flagged}
        for f in all_findings:
            o = outcome_by_id.get(id(f))
            if o:
                status = o.status
            elif id(f) in flagged_set:
                status = "FLAGGED"
            else:
                status = "PENDING"
            lines.append(_render_finding_row(f, status))
    lines.append("")

    # Per-fix diff section
    if outcomes:
        lines.append("## Applied fixes")
        lines.append("")
        for o in outcomes:
            lines.append(f"### `{o.finding.kind}` — {o.status}")
            lines.append("")
            lines.append(f"- Summary: {o.finding.summary}")
            if o.changed_files:
                lines.append(
                    f"- Files: " + ", ".join(f"`{p}`" for p in o.changed_files)
                )
            if o.diff:
                lines.append(f"- Change: {o.diff}")
            if o.error:
                lines.append(f"- Error: {o.error}")
            lines.append("")

    # Flagged-for-human section
    if flagged:
        lines.append("## Flagged for human review")
        lines.append("")
        lines.append(
            "The following findings exceed the harness's auto-fix "
            "scope. Each has a suggested approach — review and "
            "address by hand."
        )
        lines.append("")
        for f in flagged:
            lines.append(f"### `{f.kind}` — {f.severity}")
            lines.append("")
            lines.append(f"- {f.summary}")
            for key, val in (f.details or {}).items():
                if isinstance(val, (str, int, float, bool)):
                    lines.append(f"  - `{key}`: {val}")
            lines.append("")
            lines.append(f"_Suggested approach: {_suggested_approach(f)}_")
            lines.append("")

    # Grouping notes
    if plan.grouping_notes:
        lines.append("## Grouping notes")
        lines.append("")
        for n in plan.grouping_notes:
            lines.append(f"- {n}")
        lines.append("")

    # Merge instructions
    lines.append("## Review + merge")
    lines.append("")
    lines.append(f"If this report looks good, merge with:")
    lines.append("")
    lines.append("```bash")
    lines.append(f"git checkout main")
    lines.append(f"git merge --no-ff {branch_name}")
    lines.append("git push origin main")
    lines.append("```")
    lines.append("")
    lines.append("If anything looks wrong, abandon the branch:")
    lines.append("")
    lines.append("```bash")
    lines.append(f"git branch -D {branch_name}")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def _suggested_approach(f: Finding) -> str:
    """Human-readable suggestion for a flagged finding."""
    if f.kind == "test_failure" or f.kind == "test_failure_group":
        return (
            "Reproduce the failure locally; the harness considers "
            "real test failures out of scope for auto-fix because "
            "the root cause could be a logic regression."
        )
    if f.kind == "syntax_error":
        return (
            "Open the file at the reported line and resolve the "
            "syntax error. Substrate-blocking; do not merge anything "
            "else until this clears."
        )
    if f.kind == "trait_value_invalid":
        return (
            "Pick the correct trait value by consulting the ADR "
            "that introduced the role; the harness can't infer "
            "intent."
        )
    if f.kind == "tool_module_missing":
        return (
            "Either implement the missing builtin module under "
            "src/forest_soul_forge/tools/builtin/<name>.py or remove "
            "the catalog entry — but per CLAUDE.md §0 prove harm + "
            "non-load-bearing + alternative before removing."
        )
    if f.kind == "skill_unknown_tool_ref":
        return (
            "Either add the missing tool to tool_catalog.yaml or "
            "fix the skill to reference an existing tool."
        )
    if f.kind == "lint":
        return "Run `ruff check --fix` or address the warning by hand."
    return "Requires human judgment to resolve safely."


def phase5_report(
    repo_root: Path,
    *,
    branch_name: str,
    audit: AuditResult,
    plan: FixPlan,
    outcomes: list[FixOutcome],
    validation: dict,
    audit_only: bool,
) -> Path:
    """Write the report to docs/self-improvement/ and return its
    path.
    """
    log("Phase 5 — REPORT")
    REPORT_DIR_ABS = repo_root / "docs" / "self-improvement"
    REPORT_DIR_ABS.mkdir(parents=True, exist_ok=True)
    stamp = stamp_filename()
    report_path = REPORT_DIR_ABS / f"report-{stamp}.md"
    content = render_report(
        branch_name=branch_name,
        audit=audit,
        plan=plan,
        outcomes=outcomes,
        validation=validation,
        audit_only=audit_only,
    )
    report_path.write_text(content, encoding="utf-8")
    log(f"  report written: {report_path.relative_to(repo_root)}")

    # Also persist the structured findings as JSON next to the
    # markdown report — useful for trending and downstream
    # tooling.
    json_path = report_path.with_suffix(".json")
    json_payload = {
        "branch": branch_name,
        "timestamp": audit.timestamp,
        "audit": {
            "findings": [f.to_dict() for f in audit.findings],
            "pytest_summary": audit.pytest_summary,
        },
        "plan": {
            "auto_fix": [f.to_dict() for f in plan.auto_fix],
            "flagged": [f.to_dict() for f in plan.flagged],
        },
        "outcomes": [o.to_dict() for o in outcomes],
        "validation": validation,
    }
    json_path.write_text(
        json.dumps(json_payload, indent=2, default=str),
        encoding="utf-8",
    )
    return report_path


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

# Bare numeric version string per CLAUDE.md §3.
__version__ = "1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="self_improve.py",
        description=(
            "Autonomous self-improvement harness for Forest Soul "
            "Forge. Audits the project, fixes what it can prove "
            "safe, and writes a structured report for human review."
        ),
    )
    parser.add_argument(
        "--audit-only",
        action="store_true",
        help="Run the audit + report phases; skip fix + validate.",
    )
    parser.add_argument(
        "--no-branch",
        action="store_true",
        help="Don't create a git branch (run in place; report only).",
    )
    parser.add_argument(
        "--no-pytest",
        action="store_true",
        help="Skip pytest in the audit phase (config-checks only).",
    )
    args = parser.parse_args(argv)

    repo_root = REPO_ROOT
    stamp = stamp_filename()
    branch_name = f"self-improve/{stamp}"

    if not args.no_branch:
        if git_branch_exists(repo_root, branch_name):
            log(f"ERROR: branch {branch_name} already exists. Aborting.")
            return 1
        log(f"Creating branch: {branch_name}")
        git_create_branch(repo_root, branch_name)

    audit = phase1_audit(repo_root, skip_pytest=args.no_pytest)

    if args.audit_only:
        plan = phase2_analyze(audit)
        # In audit-only mode, *every* finding is flagged.
        plan = FixPlan(
            auto_fix=[],
            flagged=plan.auto_fix + plan.flagged,
            grouping_notes=plan.grouping_notes,
        )
        phase5_report(
            repo_root,
            branch_name=branch_name,
            audit=audit,
            plan=plan,
            outcomes=[],
            validation={},
            audit_only=True,
        )
        return 2

    plan = phase2_analyze(audit)
    outcomes = phase3_fix(repo_root, plan)

    validation: dict = {}
    if any(o.status == "FIXED" for o in outcomes):
        validation = phase4_validate(
            repo_root, audit.pytest_summary, outcomes,
        )
        if not args.no_branch:
            # Stage and commit the surviving fixes.
            survivor_files: list[str] = []
            for o in outcomes:
                if o.status == "FIXED":
                    survivor_files.extend(o.changed_files)
            # Deduplicate, preserve order.
            seen: set[str] = set()
            uniq: list[str] = []
            for p in survivor_files:
                if p not in seen and p not in LIVE_PATHS_IGNORE:
                    seen.add(p)
                    uniq.append(p)
            if uniq:
                git_stage_files(repo_root, uniq)
                git_commit(
                    repo_root,
                    f"chore: self-improve {stamp} — "
                    f"{len([o for o in outcomes if o.status == 'FIXED'])} fix(es)",
                )
    else:
        log("Phase 4 — VALIDATE  (nothing to validate; no fixes applied)")

    phase5_report(
        repo_root,
        branch_name=branch_name,
        audit=audit,
        plan=plan,
        outcomes=outcomes,
        validation=validation,
        audit_only=False,
    )

    if validation and validation.get("broken_tests"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
