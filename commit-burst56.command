#!/usr/bin/env bash
# Burst 56: git_diff_read.v1 — fourth Phase G.1.A primitive.
#
# read_only side_effects (git diff inspects tree/index/working copy).
# No required_initiative_level — passes any L per ADR-0021-am §5.
# SW-track Reviewer (Guardian L3) is the primary consumer.
#
# Test delta: 1693 -> 1740 passing (+47).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 56 — git_diff_read.v1 ==="
echo
clean_locks
git add src/forest_soul_forge/tools/builtin/git_diff_read.py \
        src/forest_soul_forge/tools/builtin/__init__.py \
        config/tool_catalog.yaml \
        tests/unit/test_git_diff_read_tool.py \
        commit-burst56.command
clean_locks
git status --short
echo
clean_locks
git commit -m "git_diff_read.v1 — fourth Phase G.1.A primitive (read_only)

Fourth of the 10 Phase G.1.A programming-primitive tools per the v0.2
close plan. Where git_log_read answers 'what's the history?',
git_diff_read answers 'what does this specific change look like?'.
SW-track Reviewer (Guardian L3) is the primary consumer — diffing
a feature branch against main is the canonical entry point of code
review.

Tool design:
- side_effects=read_only (git diff inspects tree/index/working copy
  state; nothing in the repo is mutated).
- No required_initiative_level — read_only passes at any L per
  ADR-0021-am §5.
- archetype_tags [observer, guardian, researcher, software_engineer]
  — broad reach (reading diffs is universal).
- Three diff modes:
  * mode='refs'    — diff between two refs (ref_a, ref_b required)
  * mode='staged'  — diff of index against HEAD (--cached)
  * mode='working' — diff of working tree against HEAD (default)
- Two-call invocation: --patch for the structured per-file diff and
  --numstat for accurate additions/deletions counts (overlaid on
  the parsed file structures). Mixing both in a single call yields
  a less cleanly parseable interleaved format.
- 30-second default timeout; 120s ceiling.

Output structure (per file):
- old_path / new_path
- status: modified | added | deleted | renamed | copied
- is_binary: bool
- additions / deletions: int (-1 for binary per numstat convention)
- hunks: list of {old_start, old_count, new_start, new_count,
  header (the @@ context line), body (raw +/-/space lines)}
- body_truncated: bool (when the per-file body cap is hit)

Truncation:
- max_files (default 100, ceiling 1000) caps file count
- max_lines_per_file (default 500, ceiling 5000) caps hunk-body
  lines per file (truncated at the last full line boundary)

Argument-injection defense:
- ref_a / ref_b rejected if they start with '-' (would smuggle
  flags into argv as positional arguments).
- ref_a / ref_b are forbidden in non-refs modes (catches confused
  callers attempting to mix modes).
- paths_filter entries are passed after \`--\` so git treats them
  as path filters, not refs. Each entry must resolve within the
  agent's allowed_paths.

Path discipline mirrors git_log_read.v1 / pytest_run.v1 /
ruff_lint.v1: allowed_paths required, resolve(strict=True) +
is_relative_to defense. Distinct refusal for not-a-git-repo.

Tests (test_git_diff_read_tool.py +47 cases):
- TestValidate (12): missing path, invalid mode, refs mode requires
  both refs, dash-prefixed refs rejected, refs forbidden in non-refs
  modes, max_files / max_lines / timeout bounds, paths_filter shape,
  valid minimal + valid refs-full + VALID_MODES constant.
- TestValidateRefString (2): normal accepted; dash-prefixed rejected.
- TestParseNumstat (5): text files, binary (-1/-1), rename arrows,
  empty input, malformed lines skipped.
- TestParseDiffOutput (8): modified, added, deleted, binary, per-
  file truncation, numstat overlay (additions/deletions),
  numstat-marks-binary, empty input.
- TestPathAllowlist (3): empty entries skipped, within-root,
  outside-blocked.
- TestExecute (14): refs mode (HEAD~2..HEAD~1 → 1 file),
  refs full history (HEAD~2..HEAD → 2 files w/ added+modified),
  working clean → empty, working dirty → 1 file, staged mode,
  paths_filter narrows, missing allowed_paths refuses, outside-
  allowed blocked, nonexistent path refuses, path-must-be-directory,
  not-a-git-repo refuses, paths_filter outside allowed refuses,
  timeout refuses, git-not-installed refuses, metadata records.
- TestRegistration (2): tool registered with read_only side_effects;
  catalog entry present.

Test delta: 1693 -> 1740 passing (+47). Zero regressions.

Path B continues: 6 Phase G.1.A primitives remaining (git_blame_read,
mypy_typecheck, semgrep_scan, tree_sitter_query, bandit_security_scan,
pip_install_isolated)."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 56 landed. git_diff_read.v1 in production. 6 Phase G.1.A primitives remaining."
echo ""
read -rp "Press Enter to close..."
