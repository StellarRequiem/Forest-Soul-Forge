#!/bin/bash
# Tag v0.5.0 final.
#
# Supersedes v0.5.0-rc (Burst 110, commit 95189a4). The substantive
# kernel work shipped end-to-end across the v0.5 arc:
#
#   ADR-0042 v0.5 Product Direction (Bursts 97-101):
#     T1 design + T2 responsive frontend + T3.1 Tauri shell +
#     T4 daemon-as-binary (PyInstaller).
#     T5 Tauri code-signing + auto-updater REMAINS DEFERRED — gated
#     on Apple Developer account decision. Operator-experience
#     polish, not kernel work; will ship in v0.5.1+ when the
#     licensing decision is made.
#
#   ADR-0043 MCP-First Plugin Protocol (Bursts 103-108, 111-113b):
#     T1 design + T2 manifest schema + T3 daemon runtime +
#     T4 audit-chain integration + T4.5 dispatcher bridge +
#     T5 example plugins + contribution guide.
#     Follow-ups #1 (per-tool requires_human_approval mirroring),
#     #2 (plugin grants substrate + operator surface), and
#     #3 (frontend Tools-tab plugin awareness) all shipped.
#     Follow-up #4 (plugin_secret_set + secrets surface) DEFERRED;
#     gated on a separate secrets-storage shape decision.
#
#   ADR-0045 Agent Posture / Trust-Light System (Bursts 113.5-115):
#     Design + T1 schema/PostureGateStep + T2 HTTP/CLI/audit +
#     T3+T4 per-grant trust_tier enforcement with red-dominates
#     precedence matrix.
#     Frontend dial widget DEFERRED (~100 LoC pure UI, no backend
#     coupling); deferred amendments (operator-session posture,
#     programmatic self-demotion, time-bounded posture, multi-
#     operator audit policy) documented in the ADR.
#
# Why v0.5.0 final ships now despite Tauri T5 + plugin_secret_set
# being deferred:
#   The kernel work (governance pipeline, posture system, plugin
#   protocol, grants, per-tool + per-grant trust dials) is the
#   load-bearing v0.5 deliverable. The deferred items are operator-
#   experience polish (signing/auto-update) and a separate feature
#   surface (secrets) — neither changes the kernel API. Holding
#   v0.5.0 hostage to a one-time Apple Developer licensing decision
#   compounds the wait. The ADR governance discipline already
#   documents the gaps; downstream releases close them.
#
# Numbers (verified against disk on 2026-05-05):
#   Test suite        2,386 passing (was 2,289 at v0.5.0-rc; +97)
#   Source LoC        50,289 (was ~48,760 at v0.5.0-rc)
#   ADRs filed        41 files / 39 unique numbers
#   Schema version    v15 (was v13 at v0.5.0-rc; +v14 grants, +v15
#                     posture)
#   Audit event types 70 (was 67 at v0.5.0-rc; +3: agent_plugin_
#                     granted, agent_plugin_revoked, agent_posture_
#                     changed)
#   Commits on main   273 (was 264 at v0.5.0-rc)
#   .command scripts  130

set -euo pipefail

cd "$(dirname "$0")"

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

echo "--- HEAD ---"
git log --oneline -1
echo ""

if git rev-parse v0.5.0 >/dev/null 2>&1; then
  echo "ERROR: tag v0.5.0 already exists. Delete it first if re-tagging."
  exit 3
fi

git tag -a v0.5.0 -m "v0.5.0 — kernel-shape governance: plugin protocol + agent posture

Supersedes v0.5.0-rc (Burst 110). Three Accepted ADRs delivered
end-to-end:

ADR-0042 v0.5 Product Direction:
  T1 design                               Burst 97
  T2 frontend responsive (PWA-first)      Burst 98
  T3.1 Tauri 2.x desktop shell            Burst 99
  T4 daemon-as-binary (PyInstaller)       Burst 101
  T5 signing + auto-updater               DEFERRED

ADR-0043 MCP-First Plugin Protocol:
  T1 design                               Burst 103
  T2 manifest schema + fsf plugin CLI     Burst 104
  T3 daemon runtime + /plugins HTTP       Burst 105
  T4 audit-chain integration              Burst 106
  T4.5 dispatcher bridge                  Burst 107
  T5 examples + contribution guide        Burst 108
  Follow-up #1 per-tool approval mirror   Burst 111
  Follow-up #3 frontend Tools-tab         Burst 112
  Follow-up #2a grants substrate          Burst 113a
  Follow-up #2b grants operator surface   Burst 113b
  Follow-up #4 plugin_secret_set          DEFERRED

ADR-0045 Agent Posture / Trust-Light System:
  Design                                  Burst 113.5
  T1 schema v15 + PostureGateStep         Burst 114
  T2 HTTP + CLI + audit event             Burst 114b
  T3+T4 per-grant trust enforcement       Burst 115

What this tag represents: the kernel-shape governance work for
Forest. Per-agent trust dial (green/yellow/red posture), per-
(agent, plugin) trust dial (grant trust_tier), per-tool approval
override (manifest requires_human_approval), all composed through
one governance pipeline, all hash-chained in the audit chain. The
foundation an agent runtime needs to be trusted with real
authority.

Numbers (verified against disk on 2026-05-05):
  Test suite       2,386 passing (was 2,289 at v0.5.0-rc; +97)
  Source LoC       50,289
  ADRs filed       41 files / 39 unique numbers
  Schema version   v15 (v13 at v0.5.0-rc; +v14 grants, +v15 posture)
  Audit events     70 (67 at v0.5.0-rc; +3 grant + posture events)
  Commits on main  273 (264 at v0.5.0-rc)

Deferred items are operator-experience polish (Tauri signing) and
a separate feature surface (plugin secrets) that don't change the
kernel API. Both gaps are documented in their respective ADRs and
will ship in v0.5.x point releases.

The v0.6 arc opens with ADR-0044 (Kernel Positioning + SoulUX
flagship branding), tracking the strategic shift from product to
kernel that this release operationalizes.
"

echo ""
echo "--- tag created ---"
git tag --list 'v0.5*' --sort=-creatordate

echo ""
echo "--- pushing tag to origin ---"
git push origin v0.5.0

echo ""
echo "=== v0.5.0 tagged + pushed ==="
echo "Press any key to close this window."
read -n 1
