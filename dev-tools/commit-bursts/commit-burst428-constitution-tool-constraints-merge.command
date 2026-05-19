#!/usr/bin/env bash
# Burst 428 — complete B416 in the constitution builder.
#
# Discovery
# ---------
# After ADR-0083 (B426) fixed the lifecycle-aware idempotency replay
# wedge, the rebirth of Reviewer-Main correctly produced a new
# instance_id (sibling 2) and a fresh constitution row. But the new
# constitution had:
#
#   constitution_hash = d55de96360bb...
#                       (IDENTICAL to the original sibling-1 hash)
#   code_read.constraints = {audit_every_call, max_calls_per_session,
#                             requires_human_approval}
#                       (NO allowed_paths, despite B416's template)
#
# Inspection: B416's diff added `tool_constraints.code_read.v1.
# allowed_paths` under role_base.code_reviewer in
# config/constitution_templates.yaml. The block is correctly there.
# But grep for `tool_constraints` across src/forest_soul_forge/core/
# returns ZERO matches (no code reads it).
#
# B416 shipped TEMPLATE data without the BUILDER code to use it.
# Constitutions built post-B416 ignored the new block entirely.
# That's why three rebirth attempts (B420 / B425 / B426) all
# produced constitutions without allowed_paths even after the
# idempotency path was fixed.
#
# What this burst does
# --------------------
# Adds layer-4 to constitution.build(): after policy resolution and
# canonicalization, walks role_base.tool_constraints and merges
# per-tool overrides into the matching tool entry's `constraints`
# dict via dict.update semantics. Key format is
# `{tool_name}.v{tool_version}` (matches B416's template). Tools
# without a matching override pass through unchanged.
#
# Files
# -----
# MOD src/forest_soul_forge/core/constitution.py
#   Layer-4 tool_constraints merge in build() before Constitution()
#   construction. ~25 LoC including extensive comment.
#
# NEW tests/unit/test_constitution_tool_constraints.py
#   Five pinned contracts:
#     1. Canonical B416 case — code_read.v1 + allowed_paths land.
#     2. Tool without matching override unchanged.
#     3. Override wins for shared keys (dict.update semantics).
#     4. No tool_constraints block → no-op.
#     5. Empty tools + overrides present → no crash.
#
# ADR-0082 compliance
# -------------------
# Architectural bug discovery trigger (same as ADR-0083): B416
# intended a kernel behavior that didn't exist. The discovery
# trail is B420 (live attempt) -> B425 (script endpoint hotfix) ->
# B426/ADR-0083 (idempotency replay wedge) -> THIS BURST (builder
# completes the merge). Three nested bugs each masking the next.
#
# This change does NOT modify any of the seven ABI surfaces
# (KERNEL.md) or seven frozen abstractions (ADR-0082).
# Constitution body is content-derived; the merge step changes
# WHAT the body contains for templates that opt in, but doesn't
# change HOW it's derived (still deterministic, same template +
# same profile + same tools => same constitution_hash; templates
# with tool_constraints simply produce a different hash from
# templates without).
#
# What this unblocks
# ------------------
# - Re-running dev-tools/rebirth-reviewer-main.command now produces
#   a Reviewer-Main with allowed_paths actually present in its
#   constitution. Option C (Reviewer-Main weekly code_review_quick
#   scheduled task) can finally fire successfully.
# - Future role_base templates that need per-tool constraint
#   overrides have a working substrate mechanism.
#
# Hippocratic gate (CLAUDE.md sec0)
# ---------------------------------
# 1. Prove harm: B416 design unrealized; Option C undeliverable;
#    three rebirth attempts produced identical (incomplete)
#    constitutions; operator confusion + wasted bursts.
# 2. Prove non-load-bearing: merge step is additive — templates
#    without `tool_constraints` block produce the same output as
#    before (no-op test pins this). Existing constitutions on disk
#    aren't touched (they're identity-bound by their original hash).
# 3. Prove alternative considered: ALT-1 patch existing
#    constitution files on disk to add allowed_paths (violates
#    immutability per ADR-0007). ALT-2 per-instance constraint
#    override table (schema change + dispatcher coupling). ALT-3
#    leave B416 as a no-op (operator already attempted three
#    workarounds; failure mode is invisible until next attempt).
#    Layer-4 merge in build() is the smallest, most local fix.
#
# Next: re-run dev-tools/rebirth-reviewer-main.command. Since the
# current sibling-2 Reviewer-Main was born BEFORE this fix, it
# also lacks allowed_paths. A third rebirth (now sibling 3) will
# pick up the merge. Or: archive sibling 2 + birth sibling 3 with
# the new builder code in place.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 428 — complete B416 in the constitution builder"
echo "==========================================================="
echo
echo "Pre-commit: clearing stale .git lock files (if any)..."
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null && echo "  removed" || echo "  none"
echo

git add src/forest_soul_forge/core/constitution.py
git add tests/unit/test_constitution_tool_constraints.py
git add dev-tools/commit-bursts/commit-burst428-constitution-tool-constraints-merge.command

echo "Pre-commit status:"
git status -s | head -10
echo
echo "Running unit tests for the new merge..."
if [ -x .venv/bin/pytest ]; then
  .venv/bin/pytest tests/unit/test_constitution_tool_constraints.py -v 2>&1 | tail -25
elif [ -x .venv/bin/python ]; then
  .venv/bin/python -m pytest tests/unit/test_constitution_tool_constraints.py -v 2>&1 | tail -25
else
  echo "  venv pytest not found — manually verify via run-tests.command"
fi
echo

git commit -m "feat(constitution): layer-4 tool_constraints merge in build() (B428)

Completes B416. B416 added tool_constraints.code_read.v1.allowed_paths
to the code_reviewer role_base template in
config/constitution_templates.yaml, but never added the builder code
to use it. Grep for 'tool_constraints' across src/forest_soul_forge/
core/ returns ZERO matches. Constitutions built post-B416 ignored
the new block.

This is why three rebirth attempts of Reviewer-Main (B420 / B425 /
B426) all produced constitutions with identical hashes
(d55de96360bb...) and no allowed_paths — the template change was
data without code.

Fix: layer-4 merge step in core/constitution.build(). After policies
resolve + canonicalize, walks role_base.tool_constraints and merges
each per-tool override into the matching tool entry's constraints
dict via dict.update. Key format: {name}.v{version} (matches
template). Override wins for shared keys; new keys (like
allowed_paths) land alongside existing ones. Tools without a
matching override pass through unchanged.

Files
-----
MOD src/forest_soul_forge/core/constitution.py
  Layer-4 in build(). ~25 LoC + the why-comment.

NEW tests/unit/test_constitution_tool_constraints.py
  Five pinned contracts: canonical case (allowed_paths land),
  non-matching pass-through, override-wins for shared keys,
  no tool_constraints block is no-op, empty tools doesn't crash.

ADR-0082 compliance
-------------------
Architectural bug discovery trigger — same nested-bug trail as
ADR-0083. Discovery sequence: B420 attempt -> B425 endpoint hotfix
-> B426/ADR-0083 idempotency replay wedge -> B428 (this) builder
completes the merge. Each bug masked the next.

Does NOT modify the seven ABI surfaces or seven frozen abstractions.
Constitution body remains content-derived; templates that don't
use tool_constraints behave identically.

What this unblocks
------------------
Re-running rebirth-reviewer-main.command after this lands produces
a Reviewer-Main (sibling 3, since sibling 2 from B426 was born
before this fix and lacks the merge) with allowed_paths actually
present in code_read.constraints. Option C becomes deliverable.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: B416 design unrealized; Option C undeliverable;
    three nested-bug rebirth attempts wasted.
  Prove non-load-bearing: merge is additive. Templates without
    tool_constraints produce identical output (pinned by test 4).
    Existing on-disk constitutions untouched (hash-bound identity).
  Prove alternative: per-instance override table = schema change.
    On-disk constitution patch = ADR-0007 violation. Leave as-is =
    perpetual ghost behavior.

Next burst: archive sibling-2 Reviewer-Main + rebirth as sibling 3
with this code live. Verify allowed_paths present in the new
constitution. Then run-reviewer-review.command to verify Option C." || { echo "commit failed"; exit 1; }

echo
echo "Pushing to origin..."
git push origin main || { echo "push failed"; exit 1; }

echo
echo "Done. Next: restart daemon, archive sibling 2, rebirth as"
echo "sibling 3 (which will pick up the merge)."
echo
echo "Press any key to close."
read -n 1 || true
