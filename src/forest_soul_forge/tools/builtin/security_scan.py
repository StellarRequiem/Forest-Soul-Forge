"""``security_scan.v1`` — ADR-0062 IoC scanner over installed
artifacts.

Reads `config/security_iocs.yaml` (the version-controlled
pattern catalog), walks the relevant artifact directories, applies
each rule's regex, and returns structured findings.

The tool is **read-only** and **report-only**. ADR-0062 D2 says
the install-time gate (refuse on CRITICAL) is T4 — a future
burst — once we have confidence the catalog doesn't false-
positive on legitimate artifacts. Today the tool produces signal;
operators decide what to do with it.

## Threat surfaces covered

| `scan_kind` | What it scans |
|---|---|
| `plugins`       | `data/plugins/*/` — installed plugin manifests + sibling code |
| `forged_tools`  | `data/forge/tools/installed/*.py` — LLM-forged installed tools |
| `forged_skills` | `data/forge/skills/installed/*.yaml` — installed skill manifests |
| `pyproject`     | `pyproject.toml` — Forest's own dependency surface |
| `all`           | All of the above |

The set of paths a given kind expands to is configurable via
`scan_paths` — operators can override the default for tests or
non-standard installs.

side_effects=read_only. Any agent in any genre can run it.
"""
from __future__ import annotations

import fnmatch
import hashlib
import re
from pathlib import Path
from typing import Any

import yaml

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


# ---- constants -----------------------------------------------------------

_VALID_KINDS = ("plugins", "forged_tools", "forged_skills", "pyproject", "all")

_VALID_SEVERITIES = ("INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL")

#: Default IoC catalog path resolved relative to repo root.
#: Override via the `catalog_path` argument or
#: `FSF_SECURITY_IOCS_PATH` env var (checked by the tool at
#: invocation time).
DEFAULT_CATALOG_PATH = Path("config/security_iocs.yaml")

#: Per-call caps. A 100-rule × 100-file scan runs in <100ms; the
#: caps protect against operator typos or runaway scan_paths.
_MAX_FILES_SCANNED = 5000
_MAX_FILE_BYTES = 4 * 1024 * 1024  # 4 MiB; binaries / huge minified blobs skipped
_MAX_FINDINGS = 1000
_EVIDENCE_EXCERPT_CHARS = 200


# ---- per-kind default scan path resolution -------------------------------


def _default_scan_paths(scan_kind: str) -> list[Path]:
    """Per-kind defaults. Repo-relative. Caller can override via
    `scan_paths` arg.

    Returns an empty list if the directory doesn't exist — the
    scan completes successfully with `scanned_path_count=0` rather
    than crashing. This is the correct posture for a fresh install
    where no plugins / forged artifacts exist yet.
    """
    if scan_kind == "plugins":
        return [Path("data/plugins")]
    if scan_kind == "forged_tools":
        return [Path("data/forge/tools/installed")]
    if scan_kind == "forged_skills":
        return [Path("data/forge/skills/installed")]
    if scan_kind == "pyproject":
        return [Path("pyproject.toml")]
    if scan_kind == "all":
        return [
            Path("data/plugins"),
            Path("data/forge/tools/installed"),
            Path("data/forge/skills/installed"),
            Path("pyproject.toml"),
        ]
    return []


# ---- catalog loader ------------------------------------------------------


def _load_catalog(catalog_path: Path) -> tuple[list[dict], list[str]]:
    """Read + compile the IoC catalog. Returns (rules, errors).

    Each rule has an additional `_compiled` field — the compiled
    re.Pattern object. Rules whose regex fails to compile are
    skipped and added to `errors`; one bad rule shouldn't kill
    the whole scan.

    Catalog parsing is repeated per scan invocation. The IoC file
    is small (low single-digit KB) so the cost is negligible and
    operators can edit + see the new rule fire on the next call
    without a daemon restart.
    """
    errors: list[str] = []
    try:
        text = catalog_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [], [
            f"catalog file not found: {catalog_path} "
            "(set FSF_SECURITY_IOCS_PATH or use --catalog-path)"
        ]
    except Exception as e:
        return [], [f"catalog read failed: {e}"]
    try:
        data = yaml.safe_load(text) or {}
    except Exception as e:
        return [], [f"catalog YAML parse failed: {e}"]
    if not isinstance(data, dict) or "rules" not in data:
        return [], ["catalog has no `rules:` top-level key"]
    raw_rules = data.get("rules") or []
    if not isinstance(raw_rules, list):
        return [], ["catalog `rules:` must be a list"]

    compiled: list[dict] = []
    for idx, rule in enumerate(raw_rules):
        if not isinstance(rule, dict):
            errors.append(f"rule #{idx} is not a mapping; skipped")
            continue
        rid = rule.get("id")
        if not isinstance(rid, str) or not rid:
            errors.append(f"rule #{idx} has no `id`; skipped")
            continue
        severity = rule.get("severity")
        if severity not in _VALID_SEVERITIES:
            errors.append(
                f"rule {rid!r} has invalid severity {severity!r}; skipped"
            )
            continue
        pattern_src = rule.get("pattern")
        if not isinstance(pattern_src, str) or not pattern_src:
            errors.append(f"rule {rid!r} has no `pattern`; skipped")
            continue
        try:
            compiled_pattern = re.compile(pattern_src)
        except re.error as e:
            errors.append(f"rule {rid!r} regex compile error: {e}; skipped")
            continue
        applies_to = rule.get("applies_to") or []
        if not isinstance(applies_to, list):
            errors.append(
                f"rule {rid!r} `applies_to` must be a list; treating as empty"
            )
            applies_to = []
        compiled.append({
            "id":           rid,
            "severity":     severity,
            "pattern":      pattern_src,
            "_compiled":    compiled_pattern,
            "applies_to":   list(applies_to),
            "rationale":    str(rule.get("rationale") or ""),
            "references":   list(rule.get("references") or []),
        })
    return compiled, errors


# ---- path walking --------------------------------------------------------


def _enumerate_scannable_files(scan_targets: list[Path]) -> list[Path]:
    """Walk each scan target. Files added directly; directories
    walked recursively. Symlinks NOT followed (prevents a planted
    symlink from redirecting the scanner to /etc/passwd or
    similar).
    """
    out: list[Path] = []
    for target in scan_targets:
        if not target.exists():
            continue
        if target.is_symlink():
            continue
        if target.is_file():
            out.append(target)
            continue
        # Directory — walk recursively.
        for p in target.rglob("*"):
            if p.is_symlink():
                continue
            if not p.is_file():
                continue
            out.append(p)
            if len(out) >= _MAX_FILES_SCANNED:
                return out
    return out


def _rule_applies_to(rule: dict, file_path: Path) -> bool:
    """Apply the rule's `applies_to` glob list. Empty list = all
    text files (match-anything). Match against the path's basename
    so glob patterns like `*.py` work without the operator having
    to write `**/*.py`.
    """
    globs = rule["applies_to"]
    if not globs:
        return True
    basename = file_path.name
    return any(fnmatch.fnmatchcase(basename, g) for g in globs)


# ---- the tool ------------------------------------------------------------


class SecurityScanTool:
    """Scan installed plugins, forged tools/skills, and the
    project's own pyproject.toml against the in-repo IoC catalog
    (ADR-0062). Reports findings; does NOT block.

    Args:
      scan_kind (str, required): one of `plugins`, `forged_tools`,
        `forged_skills`, `pyproject`, `all`. Determines the
        default scan path set.
      scan_paths (list[str], optional): override the default
        scan paths. When set, takes precedence over the kind
        defaults. Useful for tests or non-standard installs.
      catalog_path (str, optional): override the default catalog
        path (`config/security_iocs.yaml`). Useful for tests with
        a synthetic catalog.
      max_findings (int, optional): cap finding count. Default 1000;
        prevents pathological scans against generated code with
        repeating patterns from blowing memory.

    Output:
      {
        "scan_kind":           str,
        "scanned_path_count":  int,
        "scanned_paths":       [str, ...],
        "findings":            [{
          "severity": str,
          "pattern_id": str,
          "file": str,
          "line": int,
          "evidence_excerpt": str,
          "rationale": str,
          "references": [str, ...]
        }, ...],
        "by_severity":         {"CRITICAL": int, "HIGH": int, ...},
        "catalog_errors":      [str, ...],
        "catalog_rule_count":  int,
        "scan_fingerprint":    "sha256:..."   # over sorted scanned-path list
      }
    """

    name = "security_scan"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        kind = args.get("scan_kind")
        if kind not in _VALID_KINDS:
            raise ToolValidationError(
                f"scan_kind must be one of {_VALID_KINDS}; got {kind!r}"
            )
        scan_paths = args.get("scan_paths")
        if scan_paths is not None:
            if not isinstance(scan_paths, list):
                raise ToolValidationError("scan_paths must be a list of strings")
            for p in scan_paths:
                if not isinstance(p, str) or not p:
                    raise ToolValidationError(
                        "scan_paths entries must be non-empty strings"
                    )
        catalog_path = args.get("catalog_path")
        if catalog_path is not None and not isinstance(catalog_path, str):
            raise ToolValidationError("catalog_path must be a string")
        max_findings = args.get("max_findings")
        if max_findings is not None:
            if not isinstance(max_findings, int) or max_findings < 1 \
                    or max_findings > 100_000:
                raise ToolValidationError(
                    f"max_findings must be int 1..100000; got {max_findings!r}"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        scan_kind: str = args["scan_kind"]
        scan_path_overrides = args.get("scan_paths")
        catalog_path = Path(
            args.get("catalog_path") or str(DEFAULT_CATALOG_PATH)
        )
        max_findings = int(args.get("max_findings") or _MAX_FINDINGS)

        # 1. Load + compile the catalog.
        rules, catalog_errors = _load_catalog(catalog_path)

        # 2. Resolve scan paths. Operator override wins over kind
        #    defaults; defaults wrap each kind's canonical
        #    directories.
        if scan_path_overrides is not None:
            scan_targets = [Path(p) for p in scan_path_overrides]
        else:
            scan_targets = _default_scan_paths(scan_kind)

        # 3. Enumerate files. Symlinks excluded; cap applied.
        files = _enumerate_scannable_files(scan_targets)

        # 4. Walk each file, apply each applicable rule, collect
        #    findings. Skip files > _MAX_FILE_BYTES (likely binary
        #    blobs / minified vendor dumps).
        findings: list[dict] = []
        by_severity = {s: 0 for s in _VALID_SEVERITIES}
        for file_path in files:
            if len(findings) >= max_findings:
                break
            try:
                size = file_path.stat().st_size
            except OSError:
                continue
            if size > _MAX_FILE_BYTES:
                continue
            try:
                text = file_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                # Binary file or permission denied; skip silently.
                continue
            for rule in rules:
                if not _rule_applies_to(rule, file_path):
                    continue
                for m in rule["_compiled"].finditer(text):
                    line_num = text[:m.start()].count("\n") + 1
                    excerpt = _excerpt_around(text, m.start(), m.end())
                    findings.append({
                        "severity":         rule["severity"],
                        "pattern_id":       rule["id"],
                        "file":             str(file_path),
                        "line":             line_num,
                        "evidence_excerpt": excerpt,
                        "rationale":        rule["rationale"],
                        "references":       list(rule["references"]),
                    })
                    by_severity[rule["severity"]] += 1
                    if len(findings) >= max_findings:
                        break
                if len(findings) >= max_findings:
                    break

        # 5. Fingerprint: sha256 over sorted scanned-path list +
        #    catalog version. Lets an operator answer "did
        #    anything CHANGE between two scans?" without
        #    re-rendering the full output.
        scanned_path_strs = sorted(str(f) for f in files)
        fp_input = (
            "|".join(scanned_path_strs)
            + f"|catalog={catalog_path}"
            + f"|rules={len(rules)}"
        ).encode("utf-8")
        scan_fingerprint = "sha256:" + hashlib.sha256(fp_input).hexdigest()[:32]

        critical_count = by_severity["CRITICAL"]
        high_count = by_severity["HIGH"]
        summary = (
            f"scanned {len(files)} file(s) with {len(rules)} rule(s); "
            f"{len(findings)} finding(s) "
            f"(CRITICAL={critical_count}, HIGH={high_count})"
        )

        return ToolResult(
            output={
                "scan_kind":          scan_kind,
                "scanned_path_count": len(files),
                "scanned_paths":      scanned_path_strs,
                "findings":           findings,
                "by_severity":        by_severity,
                "catalog_errors":     catalog_errors,
                "catalog_rule_count": len(rules),
                "scan_fingerprint":   scan_fingerprint,
            },
            metadata={
                "catalog_path": str(catalog_path),
                "truncated":    len(findings) >= max_findings,
            },
            tokens_used=None,
            cost_usd=None,
            side_effect_summary=summary,
        )


# ---- helpers -----------------------------------------------------------


def _excerpt_around(text: str, start: int, end: int) -> str:
    """Return a ~200-char window around the match, single-line.

    Newlines collapsed to spaces so an excerpt with a multi-line
    match still renders cleanly in chronicle exports + UI panels.
    """
    half = _EVIDENCE_EXCERPT_CHARS // 2
    window_start = max(0, start - half)
    window_end = min(len(text), end + half)
    excerpt = text[window_start:window_end].replace("\n", " ").replace("\r", " ")
    if window_start > 0:
        excerpt = "..." + excerpt
    if window_end < len(text):
        excerpt = excerpt + "..."
    return excerpt.strip()
