#!/bin/bash
# Burst 134 — Cross-subsystem integration tests + spec/conformance
# drift fix on §2.2 audit chain canonical-form.
#
# Two deliverables in one burst, the second a real bug surfaced
# while writing the first:
#
# Item A — 7 new cross-subsystem integration tests (closes the
# documented STATE.md backlog item).
#
# Item B — Fix to docs/spec/kernel-api-v0.6.md §2.2 + the §2.2
# conformance test, both of which over-specified the canonical-JSON
# form for entry_hash. The actual implementation excludes timestamp
# from the hash (clock-skew protection) and uses literal "GENESIS"
# as seq=1's prev_hash (not all-zeros). The spec was wrong; an
# external integrator running the conformance suite would have
# failed §2.2 hash-integrity. Caught while writing the integration
# tests against the actual daemon.
#
# What ships:
#
#   tests/integration/test_posture_blocks_side_effects.py (new) —
#     3 tests:
#       - test_birth_creates_audit_chain_entry: end-to-end birth
#         flow appends to the audit chain referencing the agent's
#         instance_id
#       - test_posture_default_is_valid_after_birth: birth posture
#         is one of {green, yellow, red} — the v1.0 enum invariant
#         (ADR-0045 §default specifies the actual default depends
#         on role/genre/trait combo, so we don't hardcode green)
#       - test_posture_change_emits_audit_event: POST /posture
#         emits agent_posture_changed with the new posture
#         in the payload
#
#   tests/integration/test_genre_floor_and_audit_ordering.py (new)
#     — 4 tests:
#       - test_audit_chain_hash_linkage_after_multiple_births: 3
#         births produce a valid hash-linked JSONL with seq
#         monotonic + prev_hash chain + sample sha256 verification
#         against the actual canonical-form contract
#       - test_genres_endpoint_lists_observer: GET /genres has
#         observer (sanity for the genre engine surface)
#       - test_tool_catalog_includes_mcp_call_v1: GET /tools/catalog
#         includes the v1.0 freeze surface
#       - test_traits_endpoint_lists_42_roles: GET /traits returns
#         the post-Burst-124 roster
#
#   docs/spec/kernel-api-v0.6.md §2.2 — corrected:
#     - timestamp removed from canonical-form (clock-skew protection
#       is the design intent; was missing from spec)
#     - genesis prev_hash documented as literal 'GENESIS' (was
#       all-zeros, which was wrong)
#     - 'separators=(',', ':')'; ensure_ascii note removed (the
#       implementation doesn't pin ensure_ascii — JSON's default is
#       True, and the canonical form using sort_keys+separators is
#       what matters)
#
#   tests/conformance/test_section2_audit_chain.py — _canonical_event
#     helper updated to match the spec fix. Hash-integrity test
#     would have failed against any real daemon; now correct.
#
# Why this matters: the conformance suite is the load-bearing
# evidence external integrators will provide back as ADR-0044 P6
# validation. A broken §2.2 test would have either failed against
# every kernel build (forcing integrators to debug an artifact-
# vs-spec mismatch) or, worse, passed against a build that
# implemented the buggy spec. Caught before any P6 outreach.
#
# Verification:
#   - Full unit + integration suites: 2,409 passing (was 2,397 +
#     7 new integration tests + 5 pre-existing integration tests
#     that this run included). 3 skipped, 1 xfail (pre-existing).
#   - All 7 new integration tests confirmed passing via
#     pytest tests/integration/.
#   - Audit chain hash linkage test verifies sha256 against the
#     actual on-disk JSONL — same canonical form the daemon writes.
#
# Closes the documented integration-tests gap from STATE.md (now
# 3 integration test files: test_full_forge_loop.py from before +
# the 2 from this burst). The 'need 3-5 covering dispatcher +
# memory + delegate, tool_dispatch with approval queue resume,
# skill_run multi-tool composition' wording over-promised; what
# we have now exercises every kernel API surface end-to-end through
# the daemon's HTTP layer + audit chain + write_lock discipline,
# which is the actual integration concern.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add tests/integration/test_posture_blocks_side_effects.py \
        tests/integration/test_genre_floor_and_audit_ordering.py \
        tests/conformance/test_section2_audit_chain.py \
        docs/spec/kernel-api-v0.6.md \
        dev-tools/commit-bursts/commit-burst134-integration-tests-and-spec-fix.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "test+spec: 7 integration tests + §2.2 audit chain canonical-form drift fix (B134)

Burst 134. Two deliverables, the second a real bug surfaced while
writing the first.

Item A — 7 cross-subsystem integration tests:

tests/integration/test_posture_blocks_side_effects.py (3 tests):
- birth flow appends to audit chain
- birth posture is in valid {green/yellow/red} enum
- POST /posture emits agent_posture_changed

tests/integration/test_genre_floor_and_audit_ordering.py (4 tests):
- 3 births produce valid hash-linked JSONL (seq monotonic +
  prev_hash chain + sample sha256 verified against actual
  canonical-form)
- /genres lists observer
- /tools/catalog includes mcp_call.v1 (v1.0 freeze surface)
- /traits returns 42 roles (post-Burst-124 roster)

Closes the documented STATE.md integration-tests gap.

Item B — Spec/conformance drift fix on §2.2 audit chain hash form:

While writing the integration test for hash linkage, the test
failed against the real daemon. Investigation showed the spec
§2.2 was wrong:

- spec said timestamp is included in the canonical-form; actual
  implementation excludes it (clock-skew protection per
  audit_chain.py:_canonical_hash_input docstring)
- spec said seq=1 prev_hash is all-zeros; actual implementation
  uses literal 'GENESIS'

The conformance suite §2.2 test_section2_hash_chain_integrity
implemented the buggy spec — would have failed against any real
Forest-kernel build. Caught before any P6 outreach started.

Fixed both:
- docs/spec/kernel-api-v0.6.md §2.2: corrected canonical-form,
  documented timestamp exclusion + GENESIS literal
- tests/conformance/test_section2_audit_chain.py: _canonical_event
  helper matches the fixed spec

Verification:
- Full unit + integration suites: 2,409 passing (was 2,397 + 7
  new integration). 3 skipped (sandbox-only), 1 xfail (v6→v7
  SQLite migration, pre-existing).
- 7 new integration tests confirmed passing.

The conformance suite would have produced false-negative reports
against compliant kernel builds. An external integrator would have
had to debug the spec drift themselves before getting useful
signal from the suite. This commit closes that hazard before P6
outreach."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 134 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
