#!/usr/bin/env bash
# Burst 58: mypy_typecheck.v1 — sixth Phase G.1.A primitive.
#
# read_only side_effects (--no-incremental keeps it honest; no
# .mypy_cache is written). No required_initiative_level — passes
# any L per ADR-0021-am §5.
#
# Test delta: 1771 -> 1807 passing (+36).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 58 — mypy_typecheck.v1 ==="
echo
clean_locks
git add src/forest_soul_forge/tools/builtin/mypy_typecheck.py \
        src/forest_soul_forge/tools/builtin/__init__.py \
        config/tool_catalog.yaml \
        tests/unit/test_mypy_typecheck_tool.py \
        commit-burst58.command
clean_locks
git status --short
echo
clean_locks
git commit -m "mypy_typecheck.v1 — sixth Phase G.1.A primitive (read_only)

Sixth of the 10 Phase G.1.A programming-primitive tools per the v0.2
close plan. Where ruff catches style + simple logic mistakes, mypy
catches the class of bugs that come from 'I thought this was a
string'. For SW-track Engineer + Reviewer, running mypy after a
refactor is a high-yield gate before pytest.

Tool design:
- side_effects=read_only. We invoke with --no-incremental so no
  .mypy_cache is written to the agent's filesystem; this keeps the
  read_only contract honest. Older catalog versions of similar
  tools that wrote a cache are conscientiously avoided.
- No required_initiative_level — read_only passes at any L.
- archetype_tags [actuator, guardian, software_engineer]. Guardian
  reach is intentional: type errors are an inspection result, and
  Reviewer (Guardian-genre) is a primary consumer.
- Subprocess invocation order:
  * `python3 -m mypy --version` first (venv-friendly)
  * fall back to `mypy` on PATH
  Reverse of ruff's order: mypy is pure Python (no Rust-binary
  perf reason to prefer the entry-point script), and venv-internal
  installs are reliable via the module-invocation path.
- Flags: --no-incremental --show-column-numbers --show-error-codes
  --no-error-summary --no-color-output. Optional --strict (boolean
  arg). Optional --config-file=<resolved-path> (path must be
  inside allowed_paths and a regular file).
- 60-second default timeout (mypy is slower than ruff because of
  whole-program inference); 300s ceiling.

Output schema:
- findings_count / actual_count / truncated / exit_code
- findings: list of {filename, line, column, severity, code, message}

Mypy text format parser (regex-based, tolerant to missing column
or missing error-code-bracket):
  <file>:<line>[:<column>]: <severity>: <message>  [<code>?]
Severity is one of error|warning|note. Notes don't carry codes;
the regex accepts the absence cleanly.

Mypy exit-code semantics:
- 0 = clean (no errors)
- 1 = errors found (this is a normal result, not a refusal)
- 2 = command-line / config error → refused as MypyTypecheckError

Path discipline mirrors ruff_lint.v1 / pytest_run.v1:
allowed_paths required, resolve(strict=True) + is_relative_to
defense. config_file path also gated against the allowlist.

Tests (test_mypy_typecheck_tool.py +36 cases):
- TestValidate (8): missing/empty path, max_findings bounds,
  timeout bounds, invalid config_file, invalid strict (non-bool),
  valid minimal + valid full args.
- TestLocateMypy (3): module-invocation succeeds → returns
  ('python3','-m','mypy'); module fails but PATH has it → ('mypy',);
  neither works → None.
- TestParseMypyOutput (7): error w/ column + code; error w/o
  column; note w/o code; warning; summary lines skipped; empty
  input; multiple findings.
- TestPathAllowlist (2): within / outside.
- TestExecute (12): clean file → 0 findings; findings parsed; max_
  findings truncation; missing allowed_paths refuses; outside-
  allowed blocked; nonexistent path; config_file outside allowed
  refuses; config_file nonexistent refuses; --strict flag added
  to argv; --no-incremental always added (read_only contract);
  timeout refuses; mypy-not-installed refuses; mypy hard error
  (exit 2) refuses; metadata records invocation.
- TestRegistration (2): tool registered; catalog entry present
  with read_only side_effects + no required_initiative_level.

Test delta: 1771 -> 1807 passing (+36). Zero regressions.

Path B continues: 4 Phase G.1.A primitives remaining (semgrep_scan,
tree_sitter_query, bandit_security_scan, pip_install_isolated)."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 58 landed. mypy_typecheck.v1 in production. 4 Phase G.1.A primitives remaining."
echo ""
read -rp "Press Enter to close..."
