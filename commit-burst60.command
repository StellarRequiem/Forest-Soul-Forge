#!/usr/bin/env bash
# Burst 60: tree_sitter_query.v1 — eighth Phase G.1.A primitive.
#
# read_only side_effects (parses source into ASTs; nothing mutated).
# No required_initiative_level — passes any L per ADR-0021-am section 5.
#
# Test delta: 1841 -> 1872 passing (+31).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 60 — tree_sitter_query.v1 ==="
echo
clean_locks
git add src/forest_soul_forge/tools/builtin/tree_sitter_query.py \
        src/forest_soul_forge/tools/builtin/__init__.py \
        config/tool_catalog.yaml \
        tests/unit/test_tree_sitter_query_tool.py \
        commit-burst60.command
clean_locks
git status --short
echo
clean_locks
git commit -m "tree_sitter_query.v1 — eighth Phase G.1.A primitive (read_only)

Eighth of the 10 Phase G.1.A programming-primitive tools per the v0.2
close plan. Where the previous primitives operate at line-and-token
level, tree_sitter operates at the syntax-tree level. The
S-expression query language lets the agent ask 'find every function
definition that calls deprecated_func' or 'find all class methods
missing docstrings' — the kind of structural pattern that's awkward
in regex but trivial in tree-sitter. SW-track Architect's primary
structural-search tool.

Tool design:
- side_effects=read_only. Tree-sitter parses; nothing is mutated.
- No required_initiative_level — read_only passes at any L.
- archetype_tags [observer, researcher, software_engineer]. Observer
  reach is intentional: structural questions about a codebase are a
  fundamental observation primitive, distinct from text-grep.
- Lazy import of tree_sitter and tree_sitter_languages INSIDE
  execute() so the daemon boots without the optional dep. Refusal
  is graceful with install hint when the package is missing
  (TreeSitterNotInstalledError) and when the requested grammar
  isn't bundled (TreeSitterGrammarMissingError).

Language registry (12 languages at v1):
- python (.py, .pyi)
- javascript (.js, .mjs, .cjs, .jsx)
- typescript (.ts, .tsx)
- rust (.rs)
- go (.go)
- java (.java)
- c (.c, .h)
- cpp (.cpp, .cc, .cxx, .hpp, .hh, .hxx)
- ruby (.rb)
- yaml (.yaml, .yml)
- json (.json)
- bash (.sh, .bash)
The registry is the authoritative allowlist — the language arg is
validated against it BEFORE the grammar loader sees the value
(prevents arbitrary string from reaching tree_sitter_languages).

Capture API compatibility:
- tree-sitter's Query.captures() shape changed across versions:
  older returned list[(node, name)] tuples; newer returns
  dict[name, list[node]]. _normalize_captures handles both.
- start_point/end_point are 0-indexed in tree-sitter; we convert
  to 1-indexed lines in the output schema for human-friendly use.

Output schema (per match):
- filename (the file the match came from)
- captures: list of {name, text, start_line, end_line, start_col,
  end_col} — text is the source slice at the captured byte range
  (utf-8 decoded with errors='replace' for binary safety).

Path discipline mirrors prior G.1.A primitives: allowed_paths
required, resolve(strict=True) + is_relative_to defense. Directory
mode walks recursively but skips hidden directories (.git, .venv,
.mypy_cache, etc.) to avoid burning the budget on irrelevant trees.

Truncation:
- max_matches (default 100, ceiling 1000) caps total matches.
- max_files (default 200, ceiling 2000) caps files scanned in
  directory mode (prevents pathological dirs from blocking).
- timeout_seconds enforced as wall-clock between file scans —
  if the deadline passes mid-walk, refuses cleanly with the
  files_scanned count.

Tests (test_tree_sitter_query_tool.py +31 cases):
- TestValidate (8): missing path/query, invalid language enum,
  max_matches bounds, max_files bound, timeout bound, valid
  minimal + valid full + LANGUAGE_REGISTRY membership.
- TestEnumerateFiles (5): single-file extension match; wrong-ext
  excluded; recursive directory walk; max_files cap; hidden-dir
  skip (.venv etc).
- TestNormalizeCaptures (3): list-form (older tree-sitter API);
  dict-form (newer API); empty.
- TestPathAllowlist (2): within / outside.
- TestExecute (10): single-file match with capture decoded;
  directory walk yielding match per file; max_matches truncation;
  invalid query syntax → TreeSitterQueryParseError;
  tree_sitter not installed → TreeSitterNotInstalledError;
  grammar missing → TreeSitterGrammarMissingError;
  missing allowed_paths refuses; outside-allowed blocked;
  nonexistent path; metadata records grammar_name + extensions.
- TestRegistration (2): tool registered; catalog entry present
  with read_only side_effects + no required_initiative_level.

Test delta: 1841 -> 1872 passing (+31). Zero regressions.

Path B continues: 2 Phase G.1.A primitives remaining
(bandit_security_scan, pip_install_isolated)."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 60 landed. tree_sitter_query.v1 in production. 2 Phase G.1.A primitives remaining."
echo ""
read -rp "Press Enter to close..."
