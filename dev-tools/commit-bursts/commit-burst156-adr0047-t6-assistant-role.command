#!/bin/bash
# Burst 156 — ADR-0047 T6 — dedicated `assistant` role config.
#
# Replaces the interim `operator_companion` role used at birth time
# (B154) with a dedicated `assistant` role per ADR-0047 Decision 2.
# Config-only change: no kernel ABI surface touched, no daemon code
# touched. Adds the role to the four operator-customizable config
# files (per kernel/userspace boundary doc) and flips the frontend
# birth call to use it.
#
# Why a dedicated role instead of reusing operator_companion:
# operator_companion is a general user-facing advisor; the
# Persistent Assistant is purpose-built as the operator's 1:1 chat
# partner. Distinct identity in the audit chain (role=assistant
# turns are grep-able), distinct trait emphasis (higher cognitive
# weight than operator_companion since the assistant fields
# arbitrary requests; thoroughness matters more than empathy
# dominance), distinct out_of_scope additions (claim_persistent_
# self_across_births, since rebinding to a new agent is
# architecturally a NEW assistant per ADR-0001).
#
# What ships:
#
#   config/trait_tree.yaml — adds `assistant` role under roles:
#     domain_weights tuned for chat-partner posture
#     (security 0.9, audit 1.0, cognitive 1.6, communication 1.5,
#      emotional 1.3, embodiment 1.2). Cognitive weight is the
#     biggest delta from operator_companion (1.2 → 1.6).
#
#   config/constitution_templates.yaml — adds `assistant` role_base
#     under role_base:. Inherits the companion-genre harm-model
#     floor from operator_companion (forbid_credential_extraction,
#     approval_for_system_change, ADR-0038 sentience/self-modification/
#     external-support-redirect policies). Adds claim_persistent_
#     self_across_births to out_of_scope (ADR-0001 identity is per-
#     instance; rebinding makes a new persona, not a "rehydrated"
#     one). Operator duties point at the binding-reset workflow
#     instead of agent-archive.
#
#   config/tool_catalog.yaml — adds `assistant` archetype under
#     archetypes:. Standard tools mirror day_companion (llm_think,
#     memory_recall, memory_write, timestamp_window). Per ADR-0047
#     T6: "defaults to read-heavy + memory". ADR-0048 computer-
#     control tools layer on TOP via per-(agent, plugin) grants —
#     they are NOT in this constitutional baseline (grants are
#     revocable at any time without rebirthing the agent).
#
#   config/genres.yaml — companion genre claims `assistant` in its
#     roles list (after operator_companion). This pulls the
#     companion-genre risk floor (read_only/network max side
#     effects, local_only provider constraint, private memory
#     ceiling) onto the role automatically — no per-role
#     duplication needed.
#
#   frontend/js/chat.js — wireAssistantBirthFlow now sends
#     role: "assistant" (was "operator_companion"). Comment block
#     points to the new role's config locations.
#
#   frontend/index.html — birth-section copy drops the "interim"
#     caveat. Now reads:
#       Genre: companion (locked …)
#       Role: assistant (dedicated, per ADR-0047 Decision 2).
#       Computer-control capabilities layer on top via per-(agent,
#       plugin) grants — ADR-0048.
#
#   tests/unit/test_trait_engine.py — bumps the role-count assertion
#     from 42 → 43 (exact-count assertion catches role-table drift
#     by design — see test docstring). Adds a spot-check for
#     `assistant` in engine.roles.
#
# Verification:
#   - All four config YAMLs parse
#   - load_catalog().resolve_kit(role='assistant', genre='companion')
#     returns [llm_think.v1, memory_recall.v1, memory_write.v1,
#     timestamp_window.v1]
#   - constitution._require_role(load(), 'assistant') returns 5
#     policies (forbid_credential_extraction + approval_for_system_change
#     + 3 ADR-0038 policies)
#   - TraitEngine().get_role('assistant').domain_weights matches
#     the YAML
#   - GenreEngine.roles_for('companion') includes 'assistant'
#   - GenreEngine.genre_for('assistant') returns companion
#   - pytest tests/unit/test_trait_engine.py
#         tests/unit/test_genre_engine.py
#         tests/unit/test_constitution.py
#         tests/unit/test_tool_catalog.py — 194 passed
#   - Browser refresh: Assistant tab → Birth → POST /birth with
#     role=assistant succeeds; agent appears in Agents tab with the
#     new role label
#
# Closes ADR-0047 T6. Remaining ADR-0047 tranches:
#   T4: settings panel (posture, allowances → ADR-0048)
#   T5: memory_recall.v1 integration into prompt-building

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add config/trait_tree.yaml \
        config/constitution_templates.yaml \
        config/tool_catalog.yaml \
        config/genres.yaml \
        frontend/js/chat.js \
        frontend/index.html \
        tests/unit/test_trait_engine.py \
        dev-tools/commit-bursts/commit-burst156-adr0047-t6-assistant-role.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(config): ADR-0047 T6 — dedicated assistant role (B156)

Burst 156. Closes ADR-0047 T6. Replaces the interim operator_companion
role used at birth time (B154) with a dedicated assistant role per
ADR-0047 Decision 2. Config-only — no kernel ABI surface touched.

Distinct from operator_companion (general advisor) by being purpose-
built as the operator's 1:1 chat partner. Higher cognitive weight
(1.2 -> 1.6), distinct audit-chain identity (role=assistant turns
grep-able), out_of_scope adds claim_persistent_self_across_births
(ADR-0001 identity is per-instance; rebinding -> NEW assistant).

Ships across the four operator-customizable config files (per
kernel/userspace boundary doc):
- trait_tree.yaml: roles.assistant with domain_weights
- constitution_templates.yaml: role_base.assistant inheriting
  companion-genre harm-model floor (5 policies) + new
  out_of_scope entry
- tool_catalog.yaml: archetypes.assistant standard_tools
  (llm_think + memory r/w + timestamp_window — same shape as
  day_companion). Computer-control tools (ADR-0048) layer on
  top via per-(agent, plugin) grants, NOT in this constitutional
  baseline (grants are revocable without rebirthing).
- genres.yaml: companion claims assistant in its roles list.

Frontend:
- chat.js: birth call sends role='assistant'.
- index.html: drops 'interim' caveat copy.

Test:
- test_trait_engine: role count 42 -> 43 + spot-check for assistant.
- pytest tests/unit/test_{trait_engine,genre_engine,constitution,
  tool_catalog} -> 194 passed.

Closes ADR-0047 T6. Remaining: T4 (settings panel + allowances),
T5 (memory_recall integration into prompt-building)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 156 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
