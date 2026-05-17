#!/bin/bash
# Burst 354 - ADR-0079 T3: diagnostic sections 05-07.
#
# The B350-class catch zone. Three section drivers covering the
# substrate that the static sections (01-04) can't reach:
#
# 1. section-05-agent-inventory.command:
#    For each alive agent — constitution parses, every tool in
#    constitution exists in catalog, kit-tier ceiling respected
#    per genre. Replays the per-agent shape of B336 (narrow kit
#    on TestAuthor-D4) and B341 (kit-tier ceiling violation on
#    migration_pilot in guardian).
#
# 2. section-06-ctx-wiring.command:
#    THE B350 CATCH ZONE. For each subsystem the dispatcher
#    claims to wire into ToolContext (memory, delegate, audit_chain,
#    agent_registry, procedural_shortcuts, provider, personal_index,
#    secrets), probe via real dispatch of a tool depending on
#    that subsystem. Each probe runs against an alive agent. If
#    the subsystem isn't actually wired, the tool returns a
#    "not wired" error and the section flags it.
#
#    audit_chain probe uses audit_chain_verify.v1 — exact same
#    invocation that surfaced B350 originally. Pre-B350 this
#    section would have flagged audit_chain as not-wired in seconds.
#    Post-B350 it returns ok/broken structured result.
#
#    Subsystems disabled by operator opt-in (priv_client,
#    personal_index, wake_word) are detected via their tools'
#    refusal messages and counted as SKIP, not FAIL — same
#    discipline as section 03's noise filter post-B353.
#
# 3. section-07-skill-smoke.command (MVP):
#    On-disk installed skills ↔ /skills cross-check. Catches the
#    case where a skill ships under data/forge/skills/installed/
#    but isn't picked up by the daemon's skill loader (silent
#    miss). Same shape as section 04 does for tools.
#
#    Real per-skill dispatch deferred: each skill needs different
#    input fixtures + a compatible agent. A later tranche ships
#    per-skill "smoke fixtures" (a minimal valid args block bundled
#    with each manifest). MVP cross-check still catches a real
#    failure class (loader drift) cheaply.
#
# Expected first-run signals when the daemon is on B353+ code:
#   - Section 05: should PASS for every alive agent (after B353
#     fix the operator_profile_read/write registry keys align with
#     the catalog so no false catalog-mismatch on those agents).
#   - Section 06: should PASS for memory, delegate, audit_chain,
#     agent_registry, procedural_shortcuts, provider; SKIP for
#     priv_client, personal_index (opt-in defaults); secrets
#     depends on FSF_SECRETS_MASTER_KEY env state.
#   - Section 07: PASS if every installed skill is registered.
#
# Any FAIL is a real bug worth a focused fix-burst.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/diagnostic/section-05-agent-inventory.command \
        dev-tools/diagnostic/section-06-ctx-wiring.command \
        dev-tools/diagnostic/section-07-skill-smoke.command \
        dev-tools/commit-bursts/commit-burst354-adr0079-t3-sections-05-07.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(diagnostic): ADR-0079 T3 - sections 05-07 (B354)

Burst 354. The B350-class catch zone. Three section drivers
covering the substrate that the static sections (01-04) cant
reach:

  05 agent-inventory  per-agent constitution parses, tools exist
                      in catalog, kit-tier ceiling respected.
                      Replays B336 (narrow kit) + B341 (ceiling
                      violation) failure modes.
  06 ctx-wiring       THE B350 CATCH ZONE. Probes each subsystem
                      the dispatcher claims to wire into
                      ToolContext (memory, delegate, audit_chain,
                      agent_registry, procedural_shortcuts,
                      provider, personal_index, secrets) via real
                      dispatch. audit_chain probe uses
                      audit_chain_verify.v1 - the exact tool that
                      surfaced B350 originally. Opt-in defaults
                      (priv_client, personal_index) detected via
                      refusal messages and counted SKIP not FAIL.
  07 skill-smoke      MVP: on-disk installed skills cross-check
                      /skills. Catches loader drift. Real per-
                      skill dispatch deferred (needs per-skill
                      smoke fixtures).

Each section is independently runnable. The umbrella runner
(T6) wires them sequentially.

Expected first-run state with daemon on B353+ code:
  - 05 should PASS for every alive agent
  - 06 should PASS for memory/delegate/audit_chain/registry/
    procedural_shortcuts/provider; SKIP for opt-ins
  - 07 PASS if loader picked up every installed skill

Any FAIL is a real bug worth a focused fix-burst."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 354 complete - sections 05-07 shipped ==="
echo "Next: B355 - T4 sections 08-10 (audit-chain-forensics +"
echo "handoff-routing + cross-domain-orchestration)."
echo ""
echo "Press any key to close."
read -n 1 || true
