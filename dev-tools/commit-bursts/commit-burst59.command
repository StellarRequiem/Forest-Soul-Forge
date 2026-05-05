#!/usr/bin/env bash
# Burst 59: semgrep_scan.v1 — seventh Phase G.1.A primitive.
#
# read_only side_effects (semgrep is a static analyzer; no mutation).
# No required_initiative_level — passes any L per ADR-0021-am section 5.
# SW-track Reviewer (Guardian L3) is the primary consumer.
#
# Test delta: 1807 -> 1841 passing (+34).
#
# NOTE: avoiding backticks in this commit message body after the
# Burst 58 incident (cfe4219) where bash command-substitution ate
# inline code spans. Single quotes used for code references.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 59 — semgrep_scan.v1 ==="
echo
clean_locks
git add src/forest_soul_forge/tools/builtin/semgrep_scan.py \
        src/forest_soul_forge/tools/builtin/__init__.py \
        config/tool_catalog.yaml \
        tests/unit/test_semgrep_scan_tool.py \
        commit-burst59.command
clean_locks
git status --short
echo
clean_locks
git commit -m "semgrep_scan.v1 — seventh Phase G.1.A primitive (read_only)

Seventh of the 10 Phase G.1.A programming-primitive tools per the
v0.2 close plan. Where ruff_lint catches style + simple logic and
mypy_typecheck catches the type-error class, semgrep_scan catches
the class of bugs that come from 'this looks like it could be
exploited' — SQL injection, unsafe deserialization, hard-coded
secrets, taint-propagation issues. SW-track Reviewer (Guardian L3)
is the primary consumer.

Tool design:
- side_effects=read_only. Semgrep reports findings; no mutation.
- No required_initiative_level — read_only passes at any L per
  ADR-0021-amendment section 5.
- archetype_tags [guardian, security_low, software_engineer].
  security_low reach is intentional: semgrep_scan is a security-
  focused inspection tool, and security_low kit consumers
  (network-watcher, file-monitor, etc.) gain coverage of
  application-code threats they otherwise miss.
- Subprocess invocation: 'semgrep' on PATH first (faster startup
  via the binary entrypoint), 'python3 -m semgrep' as fallback.
  Flags: scan, --json, --quiet, --no-rewrite-rule-ids,
  --disable-version-check, --metrics=off. Env: SEMGREP_SEND_METRICS=off,
  SEMGREP_ENABLE_VERSION_CHECK=0, SEMGREP_USER_AGENT_APPEND=
  forest-soul-forge.
- 60-second default timeout, 600s ceiling (semgrep can be slow on
  large codebases with the 'auto' ruleset).

Output schema (per finding):
- rule_id (semgrep check_id)
- severity (one of ERROR | WARNING | INFO)
- message (human-readable rule message)
- filename / start_line / end_line / start_column / end_column
- code_snippet (the offending source lines from extra.lines)

Truncation:
- max_findings (default 500, ceiling 10000) caps entries returned.
- severity_filter narrows upstream of max_findings so a tight
  filter gives a precise read of high-severity issues without
  burning the cap budget on noise.

Argument-injection defense:
- config string rejected if it starts with '-' (would smuggle
  flags into argv as positional).
- severity_filter values constrained to {ERROR, WARNING, INFO} so
  callers cannot pass shell-meaningful tokens.
- timeout enforced as separate kwarg, not embedded in argv.
- When config is a yaml/yml local path, it must resolve within
  the agent's allowed_paths AND be a regular file. Registry refs
  ('auto', 'p/python', 'p/security-audit') bypass the path check
  by ext-suffix discrimination.

Path discipline mirrors ruff_lint.v1 / mypy_typecheck.v1:
allowed_paths required, resolve(strict=True) + is_relative_to
defense.

Semgrep exit-code semantics:
- 0  = clean (no findings)
- 1  = findings found (normal — not a refusal)
- 2+ = configuration / parse error → refused as SemgrepScanError

Tests (test_semgrep_scan_tool.py +34 cases):
- TestValidate (10): missing path/config, empty/dash-prefixed config,
  max_findings bounds, timeout bounds, severity_filter shape +
  enum, valid minimal + valid full + VALID_SEVERITIES constant.
- TestLocateSemgrep (3): PATH first, module fallback, neither.
- TestNormalizeFinding (3): full finding round-trip; missing extra
  defaults to INFO + empty message; missing positions zero out.
- TestPathAllowlist (2): within / outside.
- TestExecute (13): clean scan returns 0; findings parsed;
  severity_filter narrows ERROR/WARNING/INFO; max_findings
  truncation; yaml config must exist; yaml config outside allowed
  refuses; missing allowed_paths refuses; outside-allowed blocked;
  nonexistent path; timeout refuses; semgrep-not-installed refuses;
  hard error (exit 2) refuses; unparseable JSON refuses; metadata
  records invocation.
- TestRegistration (2): tool registered; catalog entry present.

Test delta: 1807 -> 1841 passing (+34). Zero regressions.

Path B continues: 3 Phase G.1.A primitives remaining (tree_sitter_query,
bandit_security_scan, pip_install_isolated)."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 59 landed. semgrep_scan.v1 in production. 3 Phase G.1.A primitives remaining."
echo ""
read -rp "Press Enter to close..."
