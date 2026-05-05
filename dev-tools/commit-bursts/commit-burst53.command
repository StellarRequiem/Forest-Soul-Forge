#!/usr/bin/env bash
# Burst 53: ruff_lint.v1 — Phase G.1.A first programming primitive.
#
# read_only side_effects (lint reports; never mutates files). Subprocess
# invocation of ruff CLI; fall-through to `python3 -m ruff` if not on
# PATH. Path discipline mirrors code_read.v1 (allowed_paths gate +
# resolve+is_relative_to defense). Cap on findings prevents pathological
# output. 30s default timeout (configurable up to 120s).
#
# Test delta: 1589 -> 1620 passing (+31).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 53 — ruff_lint.v1 ==="
echo
clean_locks
git add src/forest_soul_forge/tools/builtin/ruff_lint.py \
        src/forest_soul_forge/tools/builtin/__init__.py \
        config/tool_catalog.yaml \
        tests/unit/test_ruff_lint_tool.py \
        commit-burst53.command
clean_locks
git status --short
echo
clean_locks
git commit -m "ruff_lint.v1 — Phase G.1.A first programming primitive

First of the 10 Phase G.1.A programming-primitive tools per the v0.2
close plan (Path B). Gives SW-track agents (Architect, Engineer,
Reviewer) + Observer / Researcher genres a stable lint surface for
their work.

Tool design:
- side_effects=read_only. Lint reports findings; never modifies files.
  Deliberately doesn't enable --fix; tool stays honest about contract.
- No required_initiative_level (read_only passes any L per
  ADR-0021-am §5).
- archetype_tags [observer, guardian, researcher, security_low].
- Subprocess invocation of \`ruff check --output-format json --no-cache\`.
  --no-cache prevents ruff writing into the agent's working directory
  (would violate read_only contract).
- Tries \`ruff\` on PATH first; falls through to \`python3 -m ruff\`
  for venv'd environments where the entry-point script isn't on PATH.
- 30-second default timeout (configurable up to 120s); pathological
  monorepo lint shouldn't hold the dispatch.

Path discipline mirrors code_read.v1:
- allowed_paths required in constitution constraints
- resolve(strict=True) + is_relative_to defense
- Defends against ../ escape, symlink escape, case-collision tricks
- Refuses files outside allowed_paths cleanly

Output:
- findings_count, truncated, exit_code (0=clean, 1=findings, 2=hard error)
- per-finding: filename, line, column, rule_code, rule_name, message,
  severity, fixable
- metadata records ruff_invocation tuple for forensic value

Tests (test_ruff_lint_tool.py +31 cases):
- TestValidate (7): missing/empty/non-string path, invalid max_findings,
  invalid timeout_seconds; valid minimal + full args.
- TestLocateRuff (2): locates ruff when available; returns None when
  unavailable.
- TestNormalizeFinding (4): full shape, no fix, no name (falls back to
  code), missing fields don't crash.
- TestPathAllowlist (4): empty strings skipped; root match allowed;
  descendant allowed; outside path blocked.
- TestExecute (10): findings on dirty file, no findings on clean file,
  directory mode, max_findings truncation, missing allowed_paths,
  outside-allowed refusal, nonexistent path, ruff-not-installed,
  timeout, hard-error refusal, metadata records invocation, finding
  shape complete.
- TestRegistration (2): tool registered + catalog entry present.

Test delta: 1589 -> 1620 passing (+31). Zero regressions.

Path B continues: 9 more programming primitives queued for Bursts
54-62 (pytest_run, git_log_read, git_diff_read, git_blame_read,
mypy_typecheck, semgrep_scan, tree_sitter_query, bandit_security_scan,
pip_install_isolated). Then Burst 63 (docs refresh) + 64 (v0.2.0
release) close v0.2."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 53 landed. ruff_lint.v1 in production. 9 Phase G.1.A primitives remaining."
echo ""
read -rp "Press Enter to close..."
