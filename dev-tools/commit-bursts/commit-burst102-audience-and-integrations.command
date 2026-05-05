#!/usr/bin/env bash
# Burst 102: README "Who is this for?" + integrations strategy doc.
#
# Two strategic-positioning deliverables in one burst:
#
# 1. README.md — new "Who is this for?" section right after the
#    60-second pitch. Five audience segments + an explicit "NOT
#    for" list. Sets the targeting that ADR-0042 D2 (SMB +
#    prosumer) implied but the README hadn't yet stated plainly.
#
# 2. docs/roadmap/2026-05-04-integrations-strategy.md — frames
#    the next direction. Three integration categories (inbound /
#    outbound / deployment), 8 ranked recommendations, MCP-first
#    plugin thesis, LangGraph node export shape, plugin SDK
#    sketch. Sets up Burst 103 = ADR-0043 (MCP plugin protocol)
#    with the decision pre-staged.
#
# WHY NOW
#
# The user surfaced two related questions:
#   a. "Who is this for?" — the README is product-mission but
#      doesn't segment audiences. New visitors land on a dense
#      560-line README and have to infer whether it's for them.
#   b. "What can we plug into to make this more open / easier
#      plug-and-play?" — the integration story is implied (53
#      tools, MCP, skill manifests) but not framed as a roadmap.
#
# Both are positioning, not code. Lock them before sinking
# implementation into ADR-0043.
#
# WHAT'S IN THE README ADDITION
#
# Five audience segments, each with a paragraph naming the
# concrete value Forest provides them today:
#
#   - Solo developers / prosumers — local-first agent automation
#     without API-key sprawl. Free-forever per ADR-0042 D4.
#   - Security / blue-team operators — audit chain as evidence;
#     Security Swarm (ADR-0033) as canonical example.
#   - AI researchers / tinkerers — substrate for trait
#     engineering, governance experiments, multi-agent dynamics.
#   - Power users / advanced operators — sovereign runtime
#     extensible via MCP plugins (ADR-0043) and LangGraph export.
#   - Compliance-heavy teams — substrate for audit-grade work.
#     Caveat: SOC 2 / SSO / RBAC land in v1.x enterprise tier.
#
# Plus an explicit "NOT for (yet)" list:
#   - Hosted multi-tenant SaaS users (local-first by design)
#   - Zero-setup users (v0.5 fixes via Tauri installer)
#   - No-LLM-policy organizations
#   - Polished consumer chat product seekers
#
# WHAT'S IN THE INTEGRATIONS DOC
#
# - Three integration categories: inbound (Forest uses external
#   tools), outbound (external stacks use Forest agents),
#   deployment (where Forest runs).
# - 8 ranked recommendations from MCP-first plugin protocol
#   (★★★★★ leverage) down to Kubernetes Helm chart (★, conflicts
#   with v0.5 thesis, deferred).
# - MCP-first thesis preview for ADR-0043: ~/.forest/plugins/
#   directory layout, plugin.yaml schema, fsf CLI surface,
#   audit-chain integration. Why MCP over plain Python entry
#   points / OCI containers / WASM (each rejected with reasons).
# - LangGraph node export adapter shape: ~150-200 LoC of
#   forest_soul_forge.adapters.langgraph thin wrapper.
# - What's NOT in the roadmap: cloud SaaS surface for Forest
#   itself, agent marketplace, more LLM provider backends.
# - Recommended burst sequence: Burst 103 = ADR-0043, Bursts
#   104-106 = ADR-0043 T1-T3 implementation, Burst 107 =
#   LangGraph adapter, Burst 108+ = CrewAI / Docker / registry.
#
# SEQUENCE
#
# Per the orchestrator's pick: C (this burst — positioning) then
# A (Burst 103 — ADR-0043 MCP plugin protocol). C before A
# because ADR-0043's audience is the people the README addresses;
# write the audience-targeting first so the ADR's design choices
# stay aligned.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 102 — README audience + integrations strategy ==="
echo
clean_locks
git add README.md
git add docs/roadmap/2026-05-04-integrations-strategy.md
git add commit-burst102-audience-and-integrations.command
clean_locks
git status --short
echo
clean_locks
git commit -m "docs: README 'Who is this for?' + integrations strategy roadmap

Two strategic-positioning deliverables in one burst, both
prerequisites to the ADR-0043 MCP plugin protocol work in
Burst 103.

README.md — new section right after the 60-second pitch.
Five audience segments named with concrete value-prop
paragraphs:
  - Solo developers / prosumers (local-first, free forever)
  - Security / blue-team operators (audit chain + Security Swarm)
  - AI researchers / tinkerers (trait engine, genres, ADR-0036)
  - Power users / advanced operators (MCP plugins + LangGraph export)
  - Compliance-heavy teams (substrate is here; enterprise
    wrappers are v1.x)
Plus an explicit 'NOT for (yet)' list — hosted SaaS users,
zero-setup users, no-LLM-policy organizations, consumer chat
seekers.

Sets the audience-targeting that ADR-0042 D2 (SMB + prosumer
thesis) implied but the README hadn't yet stated plainly. New
visitors landing cold can now see who Forest serves before
having to infer from a dense 560-line README.

docs/roadmap/2026-05-04-integrations-strategy.md — frames the
next strategic direction. Three integration categories: inbound
(Forest uses external tools), outbound (external stacks use
Forest agents), deployment (where Forest runs). 8 ranked
recommendations from MCP-first plugin protocol (★★★★★
leverage) down to Kubernetes Helm chart (★, conflicts with
v0.5 thesis, deferred).

MCP-first thesis preview: ~/.forest/plugins/ directory layout,
plugin.yaml schema, fsf CLI surface (install/enable/disable/
secrets), audit-chain integration. Reasons MCP wins over
alternatives:
  - Plain Python plugins: full-process-privilege risk
  - OCI containers: requires container runtime on user's
    machine; misaligned with SMB segment
  - WASM plugins: Python-on-WASM still rough; reconsider when
    pyodide matures

LangGraph node export adapter shape preview: ~150-200 LoC
forest_soul_forge.adapters.langgraph wrapper. Forest agent
exposed as a LangGraph Runnable; constitution / trait profile /
audit posture stay intact.

Recommended sequence:
  Burst 103: ADR-0043 MCP plugin protocol (decision record)
  Bursts 104-106: ADR-0043 T1-T3 implementation
  Burst 107: LangGraph adapter
  Burst 108+: CrewAI / Docker / plugin registry SDK

Sets up Burst 103 with design decisions pre-staged."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 102 landed. Audience targeting locked; integrations roadmap filed."
echo "Next: Burst 103 — ADR-0043 MCP plugin protocol decision record."
echo ""
read -rp "Press Enter to close..."
