#!/bin/bash
# Burst 218 — ADR-0060 Runtime Tool Grants (draft).
#
# Today an agent's allowed tool list is frozen at birth. To add a
# catalog tool to a born agent — e.g., granting the newly-forged
# translate_to_french.v1 to operator_companion — operators must
# re-birth, which destroys the lineage.
#
# Two existing patterns in the kernel show the path forward:
# ADR-0043 follow-up #2 (agent_plugin_grants) and ADR-0053
# (per-tool plugin grants). Both stand on the same architectural
# move: augmentation tables consulted alongside the constitution,
# constitution hash immutable, audit chain records the lifecycle.
#
# ADR-0060 generalizes that pattern to catalog tools:
#   - agent_catalog_grants table (schema v16->v17, additive)
#   - dispatcher consults grants when constitution check misses
#   - posture interaction matrix (red posture cannot dispatch red
#     trust_tier grants; yellow agents downgrade red grants to
#     requires_human_approval)
#   - two new audit event types (agent_tool_granted /
#     agent_tool_revoked)
#   - tool_call_dispatched gains granted_via annotation so an
#     auditor can distinguish constitution dispatches from grant
#     dispatches
#
# 6-tranche implementation plan totaling 5 bursts.
#
# Why draft + queue rather than implement now: the dispatcher
# integration (T2) is load-bearing governance code. Cramming it
# into one burst without comprehensive tests (T5) risks silently
# breaking the constitution gate. The operator's directive was
# 'make sure nothing breaks' — so design lands now, implementation
# is a focused multi-burst arc.
#
# What we deliberately did NOT do this burst:
#   - T1-T6 implementation. Each tranche is independently shippable
#     after ADR acceptance.
#   - Rename agent_plugin_grants. The schemas differ enough that
#     unification would force awkward null columns. Recommend
#     keeping them separate.
#
# Per ADR-0001 D2: ADR-0060 explicitly preserves the immutability
#                  invariant. Implementation tranches will not
#                  mutate constitution_hash.
# Per ADR-0044 D3: zero ABI changes — pure design doc.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0060-runtime-tool-grants.md \
        dev-tools/commit-bursts/commit-burst218-adr-0060-runtime-tool-grants.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(adr-0060): draft Runtime Tool Grants (B218)

Burst 218. Closes the natural-language Forge UI loop by drafting
ADR-0060: post-birth catalog-tool grants without violating
constitution_hash immutability. Generalizes the proven
agent_plugin_grants pattern (ADR-0043 follow-up #2) to catalog
tools.

Key decisions:
- D1: dispatcher consults grants when ConstitutionGateStep misses,
      loads defaults from catalog, tags granted_via on dispatch
- D2: two new audit events (agent_tool_granted, agent_tool_revoked)
      plus granted_via annotation on tool_call_dispatched
- D3: POST/DELETE/GET endpoints under /agents/{id}/tools/grant
- D4: posture x trust_tier interaction matrix (red posture cannot
      dispatch red grants; yellow downgrades red to approval)
- D5: explicit non-goals — no constitution mutation, no
      hallucinated tools, no per-call constraint bypass

6-tranche plan totaling 5 bursts (T1 schema, T2 dispatcher, T3
endpoints, T4 posture, T5 tests, T6 frontend).

Draft status pending operator confirmation on trust_tier defaults
+ T6 scope.

Per ADR-0001 D2: explicitly preserves constitution_hash immutability.
Per ADR-0044 D3: pure design doc, zero ABI changes."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 218 complete ==="
echo "=== ADR-0060 drafted. Implementation arc queued (T1-T6). ==="
echo "Press any key to close."
read -n 1
