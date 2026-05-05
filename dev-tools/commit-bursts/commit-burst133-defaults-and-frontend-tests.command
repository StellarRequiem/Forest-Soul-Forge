#!/bin/bash
# Burst 133 — JSONSchema input defaults + frontend test scaffold.
#
# Two backlog items batched. Both have been parked in STATE.md's
# 'Items in queue' since v0.5.0 close; both have well-defined scope
# and no dependencies on each other or on outstanding decisions.
#
# Item A — JSONSchema input defaults at runtime in the skill engine.
#
#   Pre-fix: skill manifest authors could declare ``default:`` values
#   in their inputs schema, but the skill_runtime didn't apply them at
#   runtime. STATE.md documented the workaround: 'Worked around by
#   hard-coding the investigate_finding contain-threshold to literal
#   1.' Manifest authors had to reference inputs explicitly or hard-
#   code defaults inline.
#
#   Post-fix: ``_apply_schema_defaults`` helper added to
#   skill_runtime.py walks ``inputs_schema['properties']`` and merges
#   defaults for any keys the operator omitted. Operator-supplied
#   values always win — including falsy values (0, "", [], None,
#   False) — because 'operator passed it' is keyed on dict presence,
#   not truthiness. Otherwise threshold=0 (a valid integer) would
#   silently get overwritten by the default, which is the same bug
#   we're fixing at a different layer.
#
#   The merge applies only to top-level properties — we don't recurse
#   into nested object properties because the semantics get muddy
#   (do nested defaults override per-key operator values inside a
#   partial object?). Future extension if integrators ask.
#
#   Wired in at the run() entry: before binding inputs into the
#   step-walking context, defaults are merged. Existing skills that
#   don't declare defaults are unaffected.
#
# Item B — Frontend test scaffold (Vitest + jsdom).
#
#   Pre-scaffold: 0 tests for ~3,500 LoC of vanilla JS in
#   frontend/js/. Listed in STATE.md's queue as a half-day task.
#
#   Post-scaffold: Vitest + jsdom configured. Two seed test files:
#     tests/sanity.test.js — confirms the runner is wired
#       (basic assertions, document/window present, localStorage
#       works). If this fails, the scaffold is broken.
#     tests/api.test.js   — exercises api.js's documented contract
#       (URL resolution falls back to same-origin, persistence keys
#       are 'fsf.apiBase' + 'fsf.token'). Future PRs add tests
#       alongside UI changes.
#
#   Per ADR-0044 the frontend is SoulUX-userspace, not kernel — these
#   tests don't ship as part of the kernel package or the conformance
#   suite. They're for SoulUX-distribution regression coverage.
#
# What ships:
#
#   src/forest_soul_forge/forge/skill_runtime.py — adds
#     ``_apply_schema_defaults()`` helper + wires into ``run()``.
#
#   tests/unit/test_skill_runtime_input_defaults.py (new) — 11 unit
#     tests covering every documented behavior:
#       - empty schema returns inputs unchanged
#       - default fills missing key
#       - operator value wins over default (truthy + falsy)
#       - partial fill (operator passes some, defaults fill rest)
#       - properties without 'default' are skipped
#       - complex JSON values pass through
#       - defensive paths (non-dict schema, missing properties,
#         non-dict properties, non-dict per-property schema)
#
#   frontend/package.json (new) — Vitest + jsdom devDependencies.
#     Type: 'module' for ESM imports.
#
#   frontend/vitest.config.js (new) — jsdom env, tests/**/*.test.js
#     include pattern, opt-in v8 coverage.
#
#   frontend/tests/README.md (new) — usage + conventions guide.
#
#   frontend/tests/sanity.test.js (new) — scaffold-wired check.
#
#   frontend/tests/api.test.js (new) — seed test for api.js.
#
#   .gitignore — adds frontend/node_modules/, frontend/tests/coverage/,
#     frontend/.vitest-cache/. Tracks package.json, vitest.config.js,
#     tests/.
#
#   STATE.md — items-in-queue table updated. Both items now show
#     ✅ shipped status. Plus marks for B127 (P2), B128 (.command
#     archival), B129 (P3), B130+B132 (P4), B131 (P6 outreach), B126
#     (housekeeping) — the running queue is now mostly ✅ entries
#     reflecting the v0.6 arc work landed.
#
# Verification:
#   - Full unit suite: 2,397 passing (was 2,386 + 11 new defaults
#     tests). 3 skipped (sandbox-only), 1 xfail (v6→v7 SQLite
#     migration, pre-existing). Zero regressions.
#   - JSONSchema defaults helper validates against 11 edge cases
#     including the falsy-value-suppresses-default contract.
#   - Frontend scaffold parses (validated locally; npm install +
#     npm test require Node.js — operator runs that on host).
#
# Closes both backlog items. Next backlog candidate: 3-5 cross-
# subsystem integration tests (Burst 134).

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/forge/skill_runtime.py \
        tests/unit/test_skill_runtime_input_defaults.py \
        frontend/package.json \
        frontend/vitest.config.js \
        frontend/tests/ \
        .gitignore \
        STATE.md \
        dev-tools/commit-bursts/commit-burst133-defaults-and-frontend-tests.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat: JSONSchema input defaults at runtime + frontend test scaffold (B133)

Burst 133. Two long-parked backlog items batched.

Item A — JSONSchema input defaults at runtime in the skill engine.

Pre-fix: skill manifest authors could declare 'default:' values in
their inputs schema, but skill_runtime didn't apply them at runtime.
Manifest authors hard-coded defaults inline as a workaround.

Post-fix: _apply_schema_defaults helper added to skill_runtime.py
walks inputs_schema['properties'] and merges defaults for any keys
the operator omitted. Operator-supplied values always win, including
falsy values (0, '', [], None, False) — 'operator passed it' is
keyed on dict presence, not truthiness, otherwise threshold=0 (a
valid integer) would silently get overwritten.

Top-level properties only; no recursion into nested objects (semantics
get muddy with partial objects + nested defaults).

11 new unit tests in tests/unit/test_skill_runtime_input_defaults.py
covering: empty schema, default fills missing, operator wins (truthy
+ falsy), partial fill, properties without 'default' skipped, complex
JSON values, defensive paths.

Item B — Frontend test scaffold (Vitest + jsdom).

Pre-scaffold: 0 tests for ~3,500 LoC of vanilla JS in frontend/js/.

Post-scaffold:
- frontend/package.json with vitest + jsdom devDeps
- frontend/vitest.config.js (jsdom env, ESM, opt-in v8 coverage)
- frontend/tests/README.md — usage + conventions
- frontend/tests/sanity.test.js — scaffold-wired check (assertions,
  document/window, localStorage)
- frontend/tests/api.test.js — seed test for api.js's URL resolution
  + token persistence contracts

Per ADR-0044 the frontend is SoulUX-userspace, not kernel; these
tests don't ship in the kernel package or conformance suite. They're
for SoulUX-distribution regression coverage; future PRs add tests
alongside UI changes.

.gitignore updated: frontend/node_modules/, frontend/tests/coverage/,
frontend/.vitest-cache/. Tracks package.json + vitest.config.js +
tests/.

STATE.md items-in-queue table updated: both items now show ✅
shipped. Combined with B126/127/128/129/130/131/132 markings, the
running queue is now mostly ✅ entries reflecting v0.6 arc closures.

Verification:
- Full unit suite: 2,397 passing (was 2,386 + 11 new defaults
  tests). 3 skipped (sandbox-only), 1 xfail (pre-existing).
  Zero regressions.

Next backlog candidate: 3-5 cross-subsystem integration tests
(Burst 134)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 133 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
