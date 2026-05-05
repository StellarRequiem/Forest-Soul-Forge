#!/bin/bash
# ADR-0045 — Agent Posture / Trust-Light System.
#
# Design doc only. No code touched. Filed before Burst 113b so the
# operator-surface tranche there can be designed against a finalized
# posture semantics — specifically, the audit event taxonomy
# (agent_plugin_granted / agent_plugin_revoked) can include
# trust_tier in the payload from the first ship instead of needing
# a follow-up schema migration once posture lands.
#
# What ADR-0045 locks:
#
#   - Three-state per-agent posture: green / yellow / red.
#     green = honor existing per-tool/per-genre/per-grant policy as-is.
#     yellow = force pending_approval on every non-read-only dispatch.
#     red = refuse every non-read-only dispatch outright.
#   - Default posture for new agents is yellow (matches de-facto
#     behavior; migration is a no-op semantically for existing rows).
#   - Storage: schema v15 ADD COLUMN agents.posture with CHECK
#     constraint. NOT in constitution_hash — preserves the immutable-
#     constitution invariant. Posture is runtime state, like
#     agents.status.
#   - One new audit event: agent_posture_changed (the 68th event
#     type when implemented).
#   - PostureGateStep added at the END of the governance pipeline
#     (outermost authority). Reads dctx.agent_posture, escalates GO →
#     PENDING (yellow) or GO → REFUSE (red) for non-read-only tools.
#   - Per-grant trust_tier (Burst 113a forward-compat field) starts
#     being enforced as part of PostureGateStep when tool is
#     mcp_call.v1. Precedence: red dominates everything > yellow >
#     green.
#   - Operator surface: HTTP POST /agents/{id}/posture + CLI
#     `fsf agent posture set` + frontend dial.
#
# Implementation across 4 tranches:
#   T1 (Burst 114): schema v15 + PostureGateStep with agent-only
#     enforcement.
#   T2 (Burst 114b): HTTP endpoint + CLI + frontend dial + audit
#     event emit.
#   T3 (Burst 115): per-grant trust_tier enforcement folded into
#     PostureGateStep (the precedence matrix becomes live).
#   T4 (Burst 115b): tests for the 3×3 precedence matrix +
#     read-only short-circuits.
#
# Deferred to amendments (documented in the ADR):
#   - Operator-session posture (global walk-away dial).
#   - Programmatic posture changes (verifier-driven self-demotion).
#   - Audit policy on red→green transitions.
#   - Time-bounded posture.

set -euo pipefail

cd "$(dirname "$0")"

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0045-agent-posture-trust-light-system.md

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(adr): ADR-0045 Agent Posture / Trust-Light System

Design doc for kernel-grade per-agent posture system. Three-state
traffic light (green / yellow / red) that operators flip at runtime
to extend or retract trust without touching the agent's constitution.

Posture is runtime state, NOT part of constitution_hash — preserves
the immutable-constitution invariant from CLAUDE.md and ADR-0007.
Same shape as agents.status (active/archived) and agents.flagged_state
(ADR-0036): operator-set columns alongside the immutable identity.

Semantic:
  green  = honor existing per-tool / per-genre / per-grant policy
           as-is; posture adds no override
  yellow = force pending_approval on every non-read-only dispatch
           regardless of per-tool config (the I'm-watching mode)
  red    = refuse every non-read-only dispatch outright (the
           agent-on-probation mode); read-only ops still work

Default posture is 'yellow' — matches the current de-facto behavior
where most mutating tools gate via per-tool config. Migration to v15
is a semantic no-op for existing rows.

Storage: schema v15 ADD COLUMN agents.posture TEXT NOT NULL DEFAULT
'yellow' CHECK (posture IN ('green', 'yellow', 'red')). One new
audit event: agent_posture_changed (the 68th event type).

Dispatcher integration: PostureGateStep added at the END of the
governance pipeline (outermost authority). Reads dctx.agent_posture
and escalates upstream GO → PENDING for yellow or GO → REFUSE for
red on non-read-only side_effects. Upstream PENDING/REFUSE verdicts
are preserved untouched.

Per-grant trust_tier interaction: the agent_plugin_grants table from
Burst 113a stores trust_tier with the same three values. ADR-0045
T3 wires PostureGateStep to consult both agent posture AND per-grant
trust_tier when the dispatched tool is mcp_call.v1. Precedence: red
dominates > yellow > green. The trust_tier field on grants was
forward-compat storage; T3 turns it on.

Implementation across 4 tranches Bursts 114-115. Out-of-scope:
operator-session posture (global walk-away dial), programmatic
self-demotion driven by ADR-0036 verifier flags, time-bounded
posture, multi-operator audit policy on red→green transitions —
all deferred to ADR-0045 amendments.

Filed before Burst 113b (plugin grants HTTP/CLI surface) so the
audit event payloads in 113b can include trust_tier from day one
without needing a follow-up migration once T3 lands.

Credit: traffic-light formulation surfaced from a chat with Alex
2026-05-05 about giving operators a clearer trust dial than the
per-tool approval matrix. Kernel-grade framing came from the same
conversation in the context of v0.6 kernel positioning (ADR-0044,
queued)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== ADR-0045 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
