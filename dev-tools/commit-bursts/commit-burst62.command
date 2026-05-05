#!/usr/bin/env bash
# Burst 62: pip_install_isolated.v1 — TENTH Phase G.1.A primitive (CLOSES G.1.A).
#
# filesystem-tier side_effects (writes packages into venv site-packages
# + pip cache). required_initiative_level L4 (reversible-with-policy
# per ADR-0021-am section 5).
#
# Test delta: 1906 -> 1968 passing (+62).
#
# Phase G.1.A is feature-complete with this burst.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 62 — pip_install_isolated.v1 (CLOSES Phase G.1.A) ==="
echo
clean_locks
git add src/forest_soul_forge/tools/builtin/pip_install_isolated.py \
        src/forest_soul_forge/tools/builtin/__init__.py \
        config/tool_catalog.yaml \
        tests/unit/test_pip_install_isolated_tool.py \
        commit-burst62.command
clean_locks
git status --short
echo
clean_locks
git commit -m "pip_install_isolated.v1 — tenth Phase G.1.A primitive (CLOSES G.1.A)

Tenth and last of the 10 Phase G.1.A programming-primitive tools per
the v0.2 close plan. The only ACTUATOR in the batch — where the rest
of G.1.A are read_only inspection tools, this is what completes the
change loop after code_edit + pytest_run reveals a missing dependency.
SW-track Engineer (Actuator default L5) is the primary kit consumer;
this is the gateway tool for any agent-driven dependency change.

Tool design:
- side_effects=filesystem (writes to venv site-packages + pip cache).
- required_initiative_level=L4 (reversible-with-policy per
  ADR-0021-am section 5). Reasoning: a pip install is reversible
  (pip uninstall) but not trivially so — a botched install can
  leave broken metadata in site-packages. The L4 floor means
  Engineer (Actuator default L5) reaches autonomously; Reviewer
  (Guardian default L3) does NOT — must be deliberately birthed at
  ceiling L4 to use this tool. Companion (L1) refused at the
  dispatcher.
- archetype_tags [actuator, software_engineer]. Narrowest archetype
  reach in the G.1.A batch — this is the actuator that materially
  changes the agent's environment, so the kit ceiling is tighter.
- Subprocess invocation: 'venv/bin/python -m pip install
  --disable-pip-version-check --no-input [--upgrade] [--no-deps]
  -- pkg1 pkg2 ...'. The '--' separator ensures no package spec
  the validator let through can be misinterpreted as a flag.
- Refuses to CREATE venvs (existing-only) — that's a separate
  primitive yet to be filed; this tool just installs into existing
  ones via _locate_venv_python (probes bin/python, bin/python3,
  Scripts/python.exe).
- 5-minute default timeout, 30-minute ceiling.
- Stdout/stderr capped at max_log_lines (default 200, ceiling 2000)
  with truncation flags so callers can detect when to read more.

Argument-injection defense:
- Each package spec validated against PEP-503-ish grammar OR the
  VCS form 'name @ git+https://...' BEFORE being passed to pip.
  Two regex patterns: _PKG_NAME_RE for plain specs (with optional
  extras and version specifiers) and _VCS_PKG_RE for VCS form.
- Specs starting with '-' are rejected (would smuggle flags).
- Shell metacharacters (;, |, >, backtick, dollar-paren) are
  rejected by the regex.
- Test parametrize block locks down the spec validator with 13
  positive cases (requests, requests==2.31.0, fastapi[standard],
  numpy~=1.24.0, mylib @ git+https://, etc.) and 9 negative cases
  (-r requirements.txt, --upgrade, evil; rm -rf /, evil\`whoami\`,
  evil\$(echo hi), evil|cat /etc/passwd, evil>/tmp/x, etc.).

Output schema:
- venv_path / packages_requested / installed (parsed from
  'Successfully installed ...' line) / skipped (parsed from
  'Requirement already satisfied: ...' lines)
- exit_code / pip_version (best-effort) / stdout / stderr /
  stdout_truncated / stderr_truncated

Path discipline mirrors prior G.1.A primitives: allowed_paths
required, resolve(strict=True) + is_relative_to defense.

Pip exit-code semantics:
- 0 = success
- 1 = errors (network, package not found, version conflict) — we
  surface to caller via exit_code in output rather than refusing,
  so the agent can read pip's stderr and react.
- timeout = refusal (PipInstallError) since partial state may exist.

Tests (test_pip_install_isolated_tool.py +62 cases):
- TestValidate (12): missing/empty venv_path; missing packages;
  packages-not-list; invalid pkg spec (dash, shell-metas, backtick);
  invalid upgrade type; timeout bounds; max_log_lines bound; valid
  minimal; valid full; valid VCS form.
- TestIsValidPkgSpec (parametrized: 13 valid + 9 invalid =
  22 cases): the spec-validator parametrize block is the most
  important security test in the file.
- TestLocateVenvPython (2): finds POSIX layout; rejects non-venv dir.
- TestParsePipOutput (4): installed parsed; already-satisfied parsed;
  mixed; empty.
- TestCapLog (3): under-cap, over-cap, empty.
- TestPathAllowlist (2): within / outside.
- TestExecute (15): required_initiative_level class attr is L4;
  successful install parses 'Successfully installed' line;
  already-satisfied parses skipped list; failed install (exit 1)
  surfaces with exit_code 1 (not refusal); log truncation;
  --upgrade flag added; --no-deps flag added; packages always
  after '--' separator; missing allowed_paths refuses; outside-
  allowed blocked; nonexistent venv refuses; invalid venv
  structure refuses (VenvInvalidError); timeout refuses;
  pip-not-found refuses (PipNotFoundError); metadata records.
- TestRegistration (2): tool registered with side_effects=filesystem
  and required_initiative_level=L4; catalog entry asserts both.

Bug caught + fixed during build: initial fake_run captured the LAST
subprocess.run call (pip --version detection), not the install.
Fixed by capturing all calls into a list and asserting on
all_calls[0]. Documented in the test helper so future flag-add
tests don't repeat the mistake.

Test delta: 1906 -> 1968 passing (+62). Zero regressions.

==========================================================
Phase G.1.A is feature-complete.

10 primitives shipped:
1. ruff_lint.v1                read_only           L0+
2. pytest_run.v1               filesystem          L4
3. git_log_read.v1             read_only           L0+
4. git_diff_read.v1            read_only           L0+
5. git_blame_read.v1           read_only           L0+
6. mypy_typecheck.v1           read_only           L0+
7. semgrep_scan.v1             read_only           L0+
8. tree_sitter_query.v1        read_only           L0+
9. bandit_security_scan.v1     read_only           L0+
10. pip_install_isolated.v1    filesystem          L4

The change loop is now completable agent-side:
  code_read  -> read existing source
  ruff_lint  -> stylistic + logic-bug pre-check
  mypy/bandit/semgrep -> deeper static gates
  code_edit  -> propose change
  pytest_run -> run tests
  pip_install_isolated -> resolve missing dep when tests reveal it

Next: Burst 63 (docs refresh — STATE / README / CHANGELOG for
v0.2.0). Then Burst 64 (v0.2.0 release tag)."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 62 landed. pip_install_isolated.v1 in production."
echo "Phase G.1.A CLOSED. 10/10 primitives shipped."
echo "Next: Burst 63 (docs refresh) -> Burst 64 (v0.2.0 release tag)."
echo ""
read -rp "Press Enter to close..."
