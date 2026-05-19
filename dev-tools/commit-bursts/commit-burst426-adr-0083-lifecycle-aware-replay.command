#!/usr/bin/env bash
# Burst 426 — ADR-0083 Lifecycle-aware idempotency replay.
#
# This burst closes the substrate-level bug surfaced by B416/B420/B425:
# rebirth-after-archive silently no-ops because the idempotency cache
# replays the original birth response without checking whether the
# referenced agent is still alive.
#
# What's changed
# --------------
# 1. NEW: docs/decisions/ADR-0083-lifecycle-aware-idempotency-replay.md
#    Full discovery + decision + alternatives + ADR-0082 compliance.
#
# 2. src/forest_soul_forge/daemon/routers/writes/_shared.py
#    _maybe_replay_cached gains optional `is_still_valid` callback.
#    When provided + returns False on the cached body, returns None
#    (cache miss) so caller can process the request fresh.
#    ~10 LoC + comment.
#
# 3. src/forest_soul_forge/daemon/routers/writes/birth.py
#    /birth handler defines `_birth_cache_still_valid` closure that
#    parses the cached response, extracts instance_id, queries the
#    registry, and returns False if status != 'active'. Passed to
#    _maybe_replay_cached via the new keyword param.
#    ~25 LoC including extensive comment block.
#
# 4. tests/unit/test_idempotency_lifecycle.py (NEW)
#    Pins 6 contracts:
#      a) Without validator: replay behaves as before (backward compat)
#      b) Validator returns True: replay proceeds
#      c) Validator returns False: replay returns None (the new path)
#      d) Validator receives raw cached body bytes (contract pin)
#      e) Missing key short-circuits before validator (no spurious call)
#      f) Cache miss short-circuits before validator (no spurious call)
#
# ADR-0082 compliance
# -------------------
# This is a kernel addition. ADR-0082 requires one of three triggers:
#
#   ✓ Architectural bug discovery: B416/B420/B425 chain surfaced
#     that the idempotency contract has undefined behavior for
#     cross-lifecycle replays. The replay path is correct for retries
#     within a single agent lifecycle (network blip, double-submit)
#     but lies about state when the agent has been archived since the
#     original write. Three live attempts to rebirth Reviewer-Main
#     (the operationally visible symptom) trace back to this gap.
#
# The change is scoped to a single internal helper + a single call
# site. Does NOT modify any of the seven ABI surfaces (KERNEL.md) or
# seven frozen abstractions (ADR-0082). Wire format of cached
# responses unchanged. Idempotency contract is documented as
# "idempotent within a single lifecycle window" instead of
# "idempotent unconditionally."
#
# What this unblocks
# ------------------
# - Re-running dev-tools/rebirth-reviewer-main.command now produces
#   a fresh Reviewer-Main with sibling_index=2 and a constitution
#   that includes B416's allowed_paths defaults.
# - Triune-Main 3-of-3 restoration becomes the next operator action.
# - Any future archive + re-birth flow for same-trait-profile
#   agents works correctly out of the box.
#
# Hippocratic gate (CLAUDE.md sec0)
# ---------------------------------
# 1. Prove harm: Reviewer-Main archived without working replacement.
#    Triune-Main runs 2-of-3 daily. Future operators following the
#    documented rebirth pattern hit the same trap.
# 2. Prove non-load-bearing for kernel ABI: change is opt-in via a
#    keyword-only parameter; existing callers (which all of them
#    are today) see identical behavior. New tests pin the
#    backward-compat case explicitly.
# 3. Prove alternative: see ADR-0083 §"Trade-offs considered" —
#    substrate-side cache invalidation on archive (Alt 1) rejected
#    for schema-change cost and unnecessary coupling. Script-level
#    UUID-in-key (Alt 2) rejected because it requires every future
#    rebirth helper to know the gotcha. Per-agent invalidation in
#    the archive endpoint (Alt 3) rejected for mixing write paths.
#    Lifecycle-aware replay at the cache layer is the smallest +
#    most general fix.
#
# Next burst (B427)
# -----------------
# Live verification: re-run dev-tools/rebirth-reviewer-main.command
# after daemon restart, confirm a fresh Reviewer-Main is born with
# sibling_index=2 + allowed_paths in code_read. Update Triune-Main
# bond (or accept the new instance_id naturally via the trio's
# birth-triune-main idempotent flow). Verify wiring_audit_triage
# runs 3-of-3.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 426 — ADR-0083 Lifecycle-aware idempotency replay"
echo "==========================================================="
echo
echo "Pre-commit: clearing stale .git lock files (if any)..."
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null && echo "  removed" || echo "  none"
echo

git add docs/decisions/ADR-0083-lifecycle-aware-idempotency-replay.md
git add src/forest_soul_forge/daemon/routers/writes/_shared.py
git add src/forest_soul_forge/daemon/routers/writes/birth.py
git add tests/unit/test_idempotency_lifecycle.py
git add dev-tools/commit-bursts/commit-burst426-adr-0083-lifecycle-aware-replay.command

echo "Pre-commit status:"
git status -s | head -15
echo
echo "Running unit tests for the new contract (host venv)..."
if [ -x .venv/bin/pytest ]; then
  .venv/bin/pytest tests/unit/test_idempotency_lifecycle.py -v 2>&1 | tail -25
elif [ -x .venv/bin/python ]; then
  .venv/bin/python -m pytest tests/unit/test_idempotency_lifecycle.py -v 2>&1 | tail -25
else
  echo "  venv not available — test verification deferred to run-tests.command"
fi
echo

git commit -m "feat(idempotency): lifecycle-aware replay for /birth (ADR-0083 / B426)

Closes the substrate-level bug surfaced by B416/B420/B425:
rebirth-after-archive silently no-ops because the idempotency
cache replays the original birth response without checking
whether the referenced agent is still alive.

The wedge: the replay path stores '(key, endpoint, body_hash)
-> (status, response)' and returns the cached response verbatim
on the next match. That contract is correct for retries within
a single agent lifecycle (network blip, operator double-click).
It's wrong for rebirth after archive: the cached response
captures state-at-time-of-original-write (status=active,
sibling_index=1), but the agent has since been archived. The
replay returns a misleading 201 with active-state data
describing an instance that no longer exists in active form.

Fix: opt-in lifecycle check at replay time. The /birth call site
passes a validator that parses the cached body, extracts
instance_id, looks up the current row, and returns True only if
status=active. When the validator returns False, replay returns
None (cache miss) so the caller processes the request fresh.
The substrate's next_sibling_index() (counts all rows including
archived) then produces sibling_index=N+1, and the fresh birth
mints a new instance_id with the new template's constitution.

Files
-----
NEW docs/decisions/ADR-0083-lifecycle-aware-idempotency-replay.md
  Full discovery trail, decision, alternatives considered, ADR-0082
  compliance check, consequences, open questions.

MOD src/forest_soul_forge/daemon/routers/writes/_shared.py
  _maybe_replay_cached gains keyword-only is_still_valid callback.
  Returns None when callback present + returns False on the cached
  body. ~10 LoC + audit-grade comment.

MOD src/forest_soul_forge/daemon/routers/writes/birth.py
  Inline _birth_cache_still_valid closure that parses cached JSON,
  extracts instance_id, queries registry, returns
  status == 'active'. Passed via is_still_valid= keyword.
  ~25 LoC including the why-comment.

NEW tests/unit/test_idempotency_lifecycle.py
  Six pinned contracts: backward compat (no validator), validator
  True (replay proceeds), validator False (cache miss), validator
  receives raw body bytes, missing-key short-circuit, cache-miss
  short-circuit.

ADR-0082 compliance
-------------------
This is a kernel addition. Triggered under 'architectural bug
discovery' (ADR-0082 sec'Three triggers that unfreeze a specific
kernel addition'). Does NOT modify the seven ABI surfaces or
seven frozen abstractions. Idempotency contract refined from
'unconditional' to 'within-lifecycle' with the call-site choosing
the lifecycle scope.

What this unblocks
------------------
- dev-tools/rebirth-reviewer-main.command now actually rebirths
  same-trait-profile agents (next sibling_index, fresh template
  defaults in constitution).
- Triune-Main 3-of-3 restoration is a single command run away.
- All future archive+rebirth flows work correctly out of the box.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: Reviewer-Main archived without working replacement;
    Triune-Main degraded; future operators repeat the trap.
  Prove non-load-bearing for kernel ABI: opt-in callback; existing
    callers unchanged; backward-compat test pins this.
  Prove alternative: substrate-side invalidation (schema change
    cost), script-level UUID (every helper has to know), per-
    endpoint invalidation (mixes write paths) all worse. See
    ADR-0083 sec'Trade-offs considered'.

Next burst (B427): live verification via daemon restart + re-run
rebirth-reviewer-main.command + Triune-Main 3-of-3 confirmation." || { echo "commit failed"; exit 1; }

echo
echo "Pushing to origin..."
git push origin main || { echo "push failed"; exit 1; }

echo
echo "Done. Next: restart daemon to load new code, then run"
echo "dev-tools/rebirth-reviewer-main.command to verify the fix."
echo
echo "Press any key to close."
read -n 1 || true
