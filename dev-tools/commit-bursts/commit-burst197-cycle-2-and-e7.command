#!/bin/bash
# Burst 197 — ADR-0056 cycle 2 close + E7 operator helper.
#
# Lands two coherent things:
# 1. The cycle 2 deliverable: 21 passing tests for the cycle
#    decision endpoint POST /agents/.../cycles/.../decision.
# 2. The E7 operator-side helper (cycle_dispatch.py) that
#    codifies cycle 1's prompt-engineering findings (prior-cycle
#    threading + verbatim wrappers) into a reusable CLI. Cycle 2
#    was dispatched through this helper as the first real test.
#
# Bonus fix: tests/unit/test_cycles_router.py had a latent broken
# import (`from tests.unit.conftest import seed_stub_agent`) that
# was committed in B190 but never actually ran — `tests/` isn't
# a package, so the import failed at collection time. Surfaced
# while debugging cycle 2's same-shaped import. Fixed in both
# files to `from conftest import seed_stub_agent` which works
# under pytest's default --import-mode=prepend.
#
# What ships:
#
#   tests/unit/test_cycles_decision.py: NEW.
#     21 tests across 8 classes. Test architecture authored by
#     Smith via cycle 2 plan v1 (claude-sonnet-4-6, 60s, 13.6k
#     chars output). Four operator-side fixes applied at apply
#     time:
#       (a) Auth header `Authorization: Bearer X` -> `X-FSF-Token`
#           (Smith paraphrased; verbatim block didn't include the
#           auth pattern).
#       (b) Truncated TestBranchNotFound404 method completed
#           (response was clipped at 4000 max_tokens cap).
#       (c) Import path fix (see below).
#       (d) Path-traversal test split — `../etc/passwd` returns
#           404 (URL normalization layer), not 400 (regex layer).
#           Both are real defenses; the test now documents the
#           layering instead of asserting the wrong path.
#
#   tests/unit/test_cycles_router.py: latent import bug fixed.
#     `from tests.unit.conftest` -> `from conftest` so the file
#     actually imports under pytest. B190 committed this with
#     the broken import; never ran. Both files use the same
#     fix now and pass collection.
#
#   dev-tools/cycle_dispatch.py: NEW.
#     ADR-0056 E7 operator-side helper. Single Python CLI that:
#       - Reads a base prompt from --prompt-from
#       - Optionally inlines a prior cycle's response under
#         <prior_cycle> tags (--prior-response-from)
#       - Optionally wraps a list of {id, content} blocks in
#         <copy_verbatim id="..."> tags (--verbatim-from)
#       - Resolves API token from env or .env
#       - POSTs to /agents/{id}/tools/call
#       - Saves the daemon response to disk
#     No daemon changes. Audit chain unchanged. Codifies the
#     manual prompt-engineering pattern from cycle 1.5 / 1.6
#     (prior-cycle threading + verbatim markers — both ADR-0056
#     followup findings) into a reusable shape.
#
#   dev-tools/smith-cycle-2-plan.command: NEW.
#     Thin wrapper invoking cycle_dispatch.py with cycle 2 args.
#     Replaces the python-heredoc pattern from cycle 1's
#     smith-cycle-1-plan.command with a single CLI call.
#     Demonstrates the E7 helper in action.
#
#   dev-tools/smith-cycle-2-prompt.md: NEW.
#     Base prompt for cycle 2. Describes target (decision
#     endpoint), output format, and what test cases to cover.
#
#   dev-tools/smith-cycle-2-verbatim.json: NEW.
#     Two verbatim blocks: the endpoint signature condensed
#     from cycles.py, and the existing _build_workspace +
#     _build_client fixture pattern Smith should extend.
#
#   dev-tools/smith-cycle-2-plan-response-v1.json: NEW.
#     Smith's actual response (preserved for audit / E7
#     iteration evidence).
#
#   dev-tools/run-cycles-decision-tests.command: NEW.
#     Pytest runner for the new test file. Saves output to
#     cycle-2-pytest-output.txt for assistant readback.
#
#   dev-tools/cycle-2-pytest-output.txt: NEW.
#     Green output — 21 passed in 12.35s.
#
# Per ADR-0044 D3: zero ABI changes. cycle_dispatch.py is
# operator tooling, not loaded by the daemon. The daemon sees
# the same llm_think.v1 dispatch shape. Test files are pure
# additions to the unit suite.
#
# Per ADR-0001 D2: no identity surface touched. Smith's cycle 2
# dispatch is recorded in audit chain at seq=2891 via
# tool_call_succeeded; constitution + DNA unchanged.
#
# Cycle 2 vs cycle 1 measured comparison:
# - Cycle 1: 6 dispatches before clean output (1.1 vapor-target
#   -> 1.6 verbatim-compliant). Total ~3+ minutes of LLM time.
# - Cycle 2: 1 dispatch produced 8/8 intended tests. 4 operator-
#   side fixes vs cycle 1's helper-rewrite. Smith reached 90%
#   correctness on first try with E7-scaffolded prompt vs cycle
#   1's iterative discovery.
# - Tells us: prompt scaffolding (E7) closes most of the
#   iteration distance up front. Remaining gaps are paraphrase
#   risk on patterns NOT in verbatim blocks (auth headers
#   here), token-cap truncation on long outputs, and edge
#   cases the prompt didn't anticipate (URL normalization layer).

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add tests/unit/test_cycles_decision.py \
        tests/unit/test_cycles_router.py \
        dev-tools/cycle_dispatch.py \
        dev-tools/smith-cycle-2-plan.command \
        dev-tools/smith-cycle-2-prompt.md \
        dev-tools/smith-cycle-2-verbatim.json \
        dev-tools/smith-cycle-2-plan-response-v1.json \
        dev-tools/run-cycles-decision-tests.command \
        dev-tools/cycle-2-pytest-output.txt \
        dev-tools/commit-bursts/commit-burst197-cycle-2-and-e7.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(experimenter): ADR-0056 cycle 2 close + E7 helper (B197)

Burst 197. Lands cycle 2's deliverable plus the ADR-0056 E7
operator-side helper that scaffolded the dispatch.

Cycle 2 target: POST /agents/.../cycles/.../decision endpoint
unit tests. 21 tests passing in 12.35s across approve (clean +
conflict), deny (delete + preserve), counter, 400 invalid
cycle_id, 404 unknown agent, 404 branch missing, and a path-
traversal defense-in-depth test.

E7 helper: dev-tools/cycle_dispatch.py — single Python CLI
that builds the JSON body the operator was constructing
manually with python heredocs in cycle 1. Inlines prior cycle
response under <prior_cycle>, wraps verbatim blocks in
<copy_verbatim id=...> tags, POSTs the dispatch, saves
response. No daemon changes; audit chain unchanged. Codifies
cycle 1.5 + 1.6 findings (prior-cycle threading + verbatim
wrappers) into reusable infrastructure.

Latent bug fix: tests/unit/test_cycles_router.py had a broken
import (\`from tests.unit.conftest import seed_stub_agent\`)
committed in B190 — \`tests/\` isn't a package so this fails
collection. Was never actually run. Fixed in both files now to
\`from conftest import seed_stub_agent\` which works under
pytest's default --import-mode=prepend. The new
test_cycles_decision.py uses the same corrected pattern.

Cycle 2 vs cycle 1 measured comparison:
- Cycle 1: 6 dispatches before clean output (1.1 vapor-target
  -> 1.6 verbatim-compliant)
- Cycle 2: 1 dispatch produced 8/8 intended tests; 4 operator-
  side fixes at apply time (auth header, truncation,
  import path, path-traversal layer)

E7 scaffolding closes most of the iteration distance up front.
Remaining gaps are: paraphrase risk on patterns NOT in
verbatim blocks (auth header here), token-cap truncation on
long outputs, edge cases the prompt didn't anticipate (URL
normalization caught path traversal before the regex did).

Per ADR-0044 D3: zero ABI changes. cycle_dispatch.py is
operator tooling. Test files are pure additions.

Per ADR-0001 D2: no identity surface touched. Cycle 2
dispatch recorded in audit chain seq=2891."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 197 complete ==="
echo "=== ADR-0056 cycle 2 closed (21 passing). E7 helper live. ==="
echo "Press any key to close."
read -n 1
