#!/usr/bin/env bash
# Burst 55: git_log_read.v1 — third Phase G.1.A primitive.
#
# read_only side_effects (git log doesn't mutate the repo).
# No required_initiative_level — passes any L per ADR-0021-am §5.
# SW-track Architect (Observer L1+) reaches; Engineer + Reviewer too.
#
# Test delta: 1654 -> 1693 passing (+39).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 55 — git_log_read.v1 ==="
echo
clean_locks
git add src/forest_soul_forge/tools/builtin/git_log_read.py \
        src/forest_soul_forge/tools/builtin/__init__.py \
        config/tool_catalog.yaml \
        tests/unit/test_git_log_read_tool.py \
        commit-burst55.command
clean_locks
git status --short
echo
clean_locks
git commit -m "git_log_read.v1 — third Phase G.1.A primitive (read_only)

Third of the 10 Phase G.1.A programming-primitive tools per the v0.2
close plan. The shape of 'code work' that SW-track agents need
invariably starts with reading what the codebase has lived through.
git log is the canonical answer to 'what changed, by whom, when, why.'

Tool design:
- side_effects=read_only (git log inspects refs + commit objects;
  nothing in the repo is mutated).
- No required_initiative_level — read_only tools pass at any L per
  ADR-0021-am §5. SW-track Architect (Observer L1+), Engineer
  (Actuator L5), and Reviewer (Guardian L3) all reach.
- archetype_tags [observer, guardian, researcher, software_engineer]
  — broad reach since reading history is universally useful.
- Subprocess invocation: \`git -C <path> log --pretty=format:<delim>\`
  with ASCII US (\\x1f) field separator and RS (\\x1e) record
  separator. Robust against arbitrary commit-message content
  (newlines, tabs, quotes, control bytes).
- 30-second default timeout; 120s ceiling. History reads are
  normally <1s; pathological monorepos with millions of commits
  + a wide --since don't hold the dispatch indefinitely.
- max_count default 50, hard ceiling 500. Tool requests max_count+1
  internally so truncation can be detected without a second count
  query — \`truncated=true\` flagged when actual exceeds requested.

Argument-injection defense:
- ref strings rejected if they start with '-' (prevents smuggling
  flags into argv as positional). All other ref grammar is left
  to git itself to validate.
- paths_filter entries are passed after \`--\` so git treats them
  as path filters, not refs.
- Each paths_filter entry must resolve within the agent's
  allowed_paths (relative entries resolved against the repo path;
  absolute entries are validated as-is).

Output schema (per commit):
- sha (full 40-char hash)
- author_name / author_email
- author_date (%aI — ISO 8601 strict)
- commit_date (%cI — distinct from author_date for rebased commits)
- parents: list[str] (empty for root commit, multi-element for merges)
- subject (commit-message first line)
- body (rest of the commit message; trailing newlines stripped)

Path discipline mirrors ruff_lint.v1 / pytest_run.v1 / code_read.v1:
allowed_paths required, resolve(strict=True) + is_relative_to defense.
Distinct refusal for not-a-git-repo (helpful signal: path was
reachable but the repo just hasn't been initialized).

Tests (test_git_log_read_tool.py +39 cases):
- TestValidate (10): missing/empty/non-string path, max_count
  bounds, timeout bounds, ref empty/dash-prefixed, paths_filter
  shape, since/until/author shape, valid minimal + valid full args.
- TestLocateGit (2): returns path when on PATH; None when missing.
- TestValidateRefString (2): normal refs accepted; dash-prefixed
  rejected.
- TestParseLogOutput (5): empty input, single commit, root commit
  (no parents), multiple commits, defensive padding for short records.
- TestPathAllowlist (4): empty entries skipped; root match;
  descendant allowed; outside blocked.
- TestExecute (12): real-repo 3-commit roundtrip; max_count
  truncation; paths_filter narrowing; ref-specific (HEAD~2);
  missing allowed_paths refuses; outside-allowed blocked;
  nonexistent path refuses; path-must-be-directory; not-a-git-repo
  refuses cleanly; paths_filter outside allowed refuses; timeout
  refuses; git-not-installed refuses cleanly; metadata records
  invocation.
- TestRegistration (3): tool registered; catalog entry present
  with read_only side_effects + no required_initiative_level;
  no spurious initiative_level attribute on the tool object.

Test delta: 1654 -> 1693 passing (+39). Zero regressions.

Path B continues: 7 Phase G.1.A primitives remaining (git_diff_read,
git_blame_read, mypy_typecheck, semgrep_scan, tree_sitter_query,
bandit_security_scan, pip_install_isolated)."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 55 landed. git_log_read.v1 in production. 7 Phase G.1.A primitives remaining."
echo ""
read -rp "Press Enter to close..."
