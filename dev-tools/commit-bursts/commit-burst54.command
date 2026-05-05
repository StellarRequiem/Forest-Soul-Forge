#!/usr/bin/env bash
# Burst 54: pytest_run.v1 — second Phase G.1.A primitive.
#
# filesystem-tier side_effects (.pytest_cache + fixture writes).
# required_initiative_level L4 (reversible-with-policy class per
# ADR-0021-am §5). SW-track Engineer (Actuator default L5) reaches.
#
# Test delta: 1620 -> 1654 passing (+34).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 54 — pytest_run.v1 ==="
echo
clean_locks
git add src/forest_soul_forge/tools/builtin/pytest_run.py \
        src/forest_soul_forge/tools/builtin/__init__.py \
        config/tool_catalog.yaml \
        tests/unit/test_pytest_run_tool.py \
        commit-burst54.command
clean_locks
git status --short
echo
clean_locks
git commit -m "pytest_run.v1 — second Phase G.1.A primitive (filesystem L4)

Second of the 10 Phase G.1.A programming-primitive tools per the v0.2
close plan. SW-track Engineer's primary feedback loop after code_edit
+ ruff_lint — runs the actual test suite against the agent's
allowed_paths.

Tool design:
- side_effects=filesystem (pytest writes .pytest_cache + fixtures
  may mutate). archetype_tags [actuator, software_engineer].
- required_initiative_level=L4 (reversible-with-policy per
  ADR-0021-am §5). Engineer (Actuator default L5) reaches.
  Reviewer (Guardian default L3) does NOT autonomously reach;
  must be birthed at ceiling L4 deliberately. Companion (L1)
  refused at the dispatcher.
- Subprocess invocation. Prefers \`python3 -m pytest\` (venv-
  friendly); falls back to \`pytest\` PATH lookup.
- 5-minute default timeout, 30-min ceiling. Test suites take time;
  pathological hangs cleanly refuse instead of holding the
  dispatch indefinitely.
- Selectors arg accepts pytest's full selector vocabulary
  (-k expr, -m mark, test_id, etc.). Each entry passed verbatim
  as argv element; no shell parsing surface.

Output schema (structured pass/fail/skip data + per-failure
tracebacks):
- passed / failed / skipped / errors / warnings (counts)
- duration_s (parsed from summary line)
- exit_code (0=clean, 1=failures, 5=no-tests-collected — all
  treated as 'ran cleanly'; 2/3/4 are refusals)
- summary_line (the verbatim '=== X passed ... ===' terminal line)
- failures: per-test {test_id, traceback (capped), truncated}
- failures_truncated (true if count exceeded max_failures_reported)

Path discipline mirrors ruff_lint.v1 / code_read.v1: allowed_paths
required, resolve(strict=True) + is_relative_to defense.

Tests (test_pytest_run_tool.py +34 cases):
- TestValidate (9): missing/empty/non-string path, selectors must be
  list[str], invalid timeout/max_failures/max_lines bounds, valid
  minimal + full args.
- TestLocatePytest (2): locates when available; returns None when
  unavailable.
- TestParseOutput (6): all-pass summary, mixed (failed+passed+skip)
  with FAILED line capture, no-tests-collected, collection error,
  max_failures truncation, empty stdout doesn't crash.
- TestPathAllowlist (4): empty skipped, root match, descendant
  allowed, outside blocked.
- TestExecute (10): mixed results (1p/1f/1s real pytest run);
  all-pass; no-tests-collected (exit 5 OK); missing allowed_paths
  refuses; outside-allowed refuses; nonexistent path refuses;
  timeout refuses; not-installed refuses cleanly; internal error
  exit 3 refuses; metadata records invocation.
- TestRegistration (3): tool registered, catalog entry present,
  initiative_level pinned at L4.

Test delta: 1620 -> 1654 passing (+34). Zero regressions.

Path B continues: 8 Phase G.1.A primitives remaining (git_log_read,
git_diff_read, git_blame_read, mypy_typecheck, semgrep_scan,
tree_sitter_query, bandit_security_scan, pip_install_isolated)."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 54 landed. pytest_run.v1 in production. 8 Phase G.1.A primitives remaining."
echo ""
read -rp "Press Enter to close..."
