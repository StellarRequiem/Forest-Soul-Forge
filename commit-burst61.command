#!/usr/bin/env bash
# Burst 61: bandit_security_scan.v1 — ninth Phase G.1.A primitive.
#
# read_only side_effects (bandit reports findings; no mutation).
# No required_initiative_level — passes any L per ADR-0021-am section 5.
#
# Test delta: 1872 -> 1906 passing (+34).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 61 — bandit_security_scan.v1 ==="
echo
clean_locks
git add src/forest_soul_forge/tools/builtin/bandit_security_scan.py \
        src/forest_soul_forge/tools/builtin/__init__.py \
        config/tool_catalog.yaml \
        tests/unit/test_bandit_security_scan_tool.py \
        commit-burst61.command
clean_locks
git status --short
echo
clean_locks
git commit -m "bandit_security_scan.v1 — ninth Phase G.1.A primitive (read_only)

Ninth of the 10 Phase G.1.A programming-primitive tools per the v0.2
close plan. Where semgrep_scan covers multi-language patterns and
arbitrary rulesets, bandit is the canonical Python-specific security
gate — the OWASP-aligned rule set has been curated for over a decade
and catches the common Python footguns: pickle, exec, shell=True,
weak hashing, hardcoded secrets pattern, flask debug=True. SW-track
Reviewer + Guardian-genre security_low consumers reach.

Tool design:
- side_effects=read_only.
- No required_initiative_level — read_only passes any L.
- archetype_tags [guardian, security_low, software_engineer].
- Subprocess invocation: 'bandit' on PATH first, 'python3 -m bandit'
  fallback. Flags: -f json, -q (quiet), -r when target is a dir,
  threshold flags -l/-ll/-lll for severity and -i/-ii/-iii for
  confidence (bandit's count-of-l convention is stable across 1.7+).
- Optional skip_tests list (B-codes; validated against pattern
  ^B[0-9]{3}$) forwarded as --skip arg.
- 60-second default timeout, 600s ceiling.

Output schema (per finding):
- test_id (B101, B102, ...)
- test_name (exec_used, hardcoded_password_string, ...)
- severity (LOW | MEDIUM | HIGH)
- confidence (LOW | MEDIUM | HIGH)
- filename / line / message / code_snippet / more_info (URL)

Argument-injection defense:
- severity_level + confidence_level constrained to enum
  {low, medium, high}.
- skip_tests entries validated against ^B[0-9]{3}$ regex —
  rejects arbitrary strings or shell metachars.
- timeout enforced as separate kwarg.

Path discipline mirrors prior G.1.A primitives: allowed_paths
required, resolve(strict=True) + is_relative_to defense.

Bandit exit-code semantics:
- 0 = no findings
- 1 = findings found (normal, not refusal)
- 2+ = errors during scan → refused as BanditScanError

Bug caught + fixed during build: my initial flag construction
double-counted the 'l' character (output -llll for high severity
instead of -lll). Test caught it; fixed by emitting the count-of-l
sequence directly with a single dash prefix, not embedding the
literal 'l' in the format string. Documented in the in-line comment
so future-me doesn't re-introduce.

Tests (test_bandit_security_scan_tool.py +34 cases):
- TestValidate (10): missing path; invalid severity/confidence
  enum; invalid skip_tests (string-not-list, bad-format, too-short);
  max_findings + timeout bounds; valid minimal + valid full +
  VALID_LEVELS constant.
- TestSeverityFlag (2): _severity_to_flag and _confidence_to_flag
  produce 'l'/'ll'/'lll' and 'i'/'ii'/'iii'.
- TestLocateBandit (3): PATH first, module fallback, neither.
- TestNormalizeFinding (2): full finding + missing-fields defaults.
- TestPathAllowlist (2): within / outside.
- TestExecute (13): clean scan, findings parsed, max_findings
  truncation, severity flag added correctly (regression test for
  the -llll bug), skip_tests forwarded as --skip B101,B404,
  -r flag added for directories, missing allowed_paths refuses,
  outside-allowed blocked, nonexistent path, timeout refuses,
  bandit-not-installed refuses, hard error (exit 2) refuses,
  unparseable JSON refuses, metadata records.
- TestRegistration (2): tool registered; catalog entry present.

Test delta: 1872 -> 1906 passing (+34). Zero regressions.

Path B continues: 1 Phase G.1.A primitive remaining
(pip_install_isolated — the only filesystem-tier tool of the set,
required_initiative_level L4)."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 61 landed. bandit_security_scan.v1 in production. 1 Phase G.1.A primitive remaining."
echo ""
read -rp "Press Enter to close..."
