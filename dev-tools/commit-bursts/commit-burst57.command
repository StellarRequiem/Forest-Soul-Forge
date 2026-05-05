#!/usr/bin/env bash
# Burst 57: git_blame_read.v1 — fifth Phase G.1.A primitive.
#
# read_only side_effects (git blame inspects refs + file content).
# No required_initiative_level — passes any L per ADR-0021-am §5.
# SW-track Reviewer + Architect both reach.
#
# Test delta: 1740 -> 1771 passing (+31).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 57 — git_blame_read.v1 ==="
echo
clean_locks
git add src/forest_soul_forge/tools/builtin/git_blame_read.py \
        src/forest_soul_forge/tools/builtin/__init__.py \
        config/tool_catalog.yaml \
        tests/unit/test_git_blame_read_tool.py \
        commit-burst57.command
clean_locks
git status --short
echo
clean_locks
git commit -m "git_blame_read.v1 — fifth Phase G.1.A primitive (read_only)

Fifth of the 10 Phase G.1.A programming-primitive tools per the v0.2
close plan. Where git_log_read answers 'what's the history?' and
git_diff_read answers 'what does this change look like?',
git_blame_read answers 'who last touched this specific line, when,
and in what commit?'.

Tool design:
- side_effects=read_only (git blame inspects commit objects + the
  file at the requested ref; nothing is mutated).
- No required_initiative_level — passes at any L per ADR-0021-am §5.
- archetype_tags [observer, guardian, researcher, software_engineer]
  — broad reach; SW-track Reviewer + Architect both load this kit.
- Subprocess invocation: \`git -C <parent_dir> blame --porcelain
  [-L start,end] <ref> -- <basename>\`. Invoking from the file's
  parent directory keeps the cwd inside the agent's allowed tree
  even for deeply-nested files.
- Porcelain stream parsed with a stateful accumulator: a per-sha
  metadata cache means subsequent lines from the same commit
  inherit the cached author/summary without re-emitted headers
  (which is what porcelain optimizes on the wire).
- 30-second default timeout, 120s ceiling.

Output schema (per line):
- line_no (1-indexed in the current file at the requested ref)
- original_line_no (1-indexed in the commit's version of the file —
  useful for tracking diff-rename history)
- sha (full 40-char hash)
- author_name / author_email
- author_date (ISO 8601 with timezone offset preserved from the
  porcelain stream — committer-time/tz are dropped at v1 since
  no consumer has asked yet)
- summary (commit subject)
- content (the actual line content, tab-prefix stripped)

Argument-injection defense:
- ref strings rejected if they start with '-' (prevents flag
  smuggling via positional argv).
- line_range bounds validated (start >= 1, end >= 1, start <= end).
- Path must be a regular file — refuses directories cleanly
  (git blame is per-file by design).

Path discipline mirrors git_log_read.v1 / git_diff_read.v1 /
ruff_lint.v1: allowed_paths required, resolve(strict=True) +
is_relative_to defense. Distinct refusal for not-a-git-repo.

Truncation:
- max_lines (default 500, ceiling 5000) caps entries returned.
- line_range narrows the scan upstream of max_lines so a tight
  range gives a precise read of a hot section without burning
  the cap budget.

Tests (test_git_blame_read_tool.py +31 cases):
- TestValidate (8): missing path, invalid ref (empty + dash-prefixed),
  invalid line_range (wrong shape, non-list, zero start, end<start),
  invalid max_lines / timeout bounds, valid minimal + valid full args.
- TestValidateRefString (2): normal refs accepted; dash rejected.
- TestFormatUnixWithTz (3): UTC, -0400 offset, malformed-tz fallback
  to UTC ISO.
- TestParsePorcelain (3): single blame group; multiple commits with
  per-sha metadata cache reuse; empty input.
- TestPathAllowlist (2): within / outside.
- TestExecute (12): real-blame returns 6 lines for a 3-commit file
  with two distinct authors (Alex + Beth); line_range narrows;
  truncation; ref-specific (HEAD~2 → 2 lines); missing allowed_paths
  refuses; outside-allowed blocked; path-must-be-file (refuses dir);
  nonexistent path; not-a-git-repo refuses; timeout refuses;
  git-not-installed refuses; metadata records.
- TestRegistration (2): tool registered with read_only side_effects;
  catalog entry present without required_initiative_level.

Test delta: 1740 -> 1771 passing (+31). Zero regressions.

Path B continues: 5 Phase G.1.A primitives remaining (mypy_typecheck,
semgrep_scan, tree_sitter_query, bandit_security_scan,
pip_install_isolated)."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 57 landed. git_blame_read.v1 in production. 5 Phase G.1.A primitives remaining."
echo ""
read -rp "Press Enter to close..."
