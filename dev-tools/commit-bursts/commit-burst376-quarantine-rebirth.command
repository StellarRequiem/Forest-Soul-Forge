#!/bin/bash
# Burst 376 - quarantine rebirth: lineage doc + clear manifest.
#
# Operator decision (2026-05-17 evening): the three quarantined
# agents (Kraine / Victor / chaz, born 2026-05-07 with broken
# free-text 'override' append at constitution EOF) get rebirthed
# - archive the old instance + birth a new one with the same role
# + agent_name, capturing the (old, new) lineage pair into the
# audit chain through the archive_event + agent_created events.
#
# The rebirth driver (dev-tools/rebirth-quarantined-agents.command)
# already ran live against the daemon and produced:
#   data/test-runs/rebirth-2026-05-17.json (machine-readable
#   lineage record)
#   data/test-runs/rebirth-2026-05-17.pairs.tsv (per-line lineage)
#
# Lineage:
#   Kraine    system_architect_054edc592917      -> system_architect_946d6c0cad98
#   Victor    knowledge_consolidator_9dd33078e7bd -> knowledge_consolidator_13ff42f35f82
#   chaz      software_engineer_871a237714a1     -> software_engineer_c1be854eadef
#
# This commit lands the operator-readable narrative + clears the
# quarantine manifest. The rebirth driver script itself stays in
# dev-tools/ as a referenceable shape for future quarantine
# resolutions; reusable if a different operator chooses rebirth
# for a future broken agent.
#
# Files in this commit:
#   docs/audits/2026-05-17-quarantine-rebirth.md (NEW)
#     Operator-readable lineage doc. Pre-rebirth state, the
#     'why no YAML repair' rationale (CLAUDE.md identity-hash
#     invariant), the rebirth operation log, what dropped (the
#     manually-appended override prose), where future agent-
#     specific guidance lives (ADR-0036 posture + ADR-0072
#     provenance preferences - NOT free text in constitution YAML).
#   config/agent_quarantine.yaml (MOD)
#     Three entries removed; manifest now `entries: []` with a
#     header comment pointing at the audit doc + JSON log.
#   dev-tools/rebirth-quarantined-agents.command (NEW)
#     The driver. Idempotent: re-running on already-rebirthed
#     state is safe (404 on already-archived; idempotency key
#     short-circuits a duplicate birth). Reusable shape for
#     future quarantine rebirth needs.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm of NOT rebirthing: the three agents stay in
#     quarantine indefinitely; they're effectively non-functional
#     (constitution unparseable -> dispatcher can't load
#     constraints), so they're zombie surface area without
#     operational value.
#   Prove non-load-bearing: rebirth path goes through the proper
#     pipeline. /archive emits agent_archived audit chain event;
#     /birth emits agent_created with a fresh constitution_hash.
#     Identity invariant preserved (we did NOT mutate the old
#     constitution YAML).
#   Prove alternative is strictly better:
#     - Archive-only: drops the agent name. Operator likes the
#       names; rebirth preserves them.
#     - Repair-YAML: violates identity-hash immutability.
#     - Leave-in-quarantine: zombie surface area.
#     Rebirth is the only path that preserves identity invariant
#     + agent-name continuity + actually restores operational
#     state.
#
# Verification after this commit lands:
#   1. diagnostic-all section-05 - old instance ids are gone
#      (archived); new instance ids surface as PASS with clean
#      constitution parse.
#   2. examples/audit_chain.jsonl - 6 new entries (3 archived
#      + 3 created) from the rebirth run.
#   3. config/agent_quarantine.yaml - entries: [] with header
#      comment.
#   4. docs/audits/2026-05-17-quarantine-rebirth.md - operator-
#      readable narrative.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/audits/2026-05-17-quarantine-rebirth.md \
        config/agent_quarantine.yaml \
        dev-tools/rebirth-quarantined-agents.command \
        dev-tools/commit-bursts/commit-burst376-quarantine-rebirth.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(governance): quarantine rebirth lineage record (B376)

Burst 376. Land the operator-readable lineage doc + clear the
quarantine manifest after Kraine / Victor / chaz rebirth.

Operator decision (2026-05-17 evening): the three quarantined
agents born 2026-05-07 with broken free-text 'override' append
get rebirthed - archive the old instance via /archive + mint a
new one via /birth with the same role + agent_name. Identity-
hash invariant preserved (CLAUDE.md: 'Constitution hash is
immutable per agent' - we did NOT touch the old YAML).

Lineage (machine-readable: data/test-runs/rebirth-2026-05-17.json):
  Kraine    system_architect_054edc592917      -> system_architect_946d6c0cad98
  Victor    knowledge_consolidator_9dd33078e7bd -> knowledge_consolidator_13ff42f35f82
  chaz      software_engineer_871a237714a1     -> software_engineer_c1be854eadef

Files:
  docs/audits/2026-05-17-quarantine-rebirth.md (NEW)
    Pre-rebirth state, identity-hash rationale, operation log,
    what dropped (manually-appended override prose), where future
    agent-specific guidance belongs (ADR-0036 posture / ADR-0072
    provenance preferences - NOT free text in constitution YAML).
  config/agent_quarantine.yaml (MOD)
    Three entries removed; manifest now entries: [] with header
    pointing at the audit doc + JSON log.
  dev-tools/rebirth-quarantined-agents.command (NEW)
    Driver. Idempotent (404 on already-archived; idempotency key
    short-circuits duplicate birth). Reusable shape for future
    quarantine rebirths.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm of NOT rebirthing: zombie agents indefinitely
    surface in the registry but can't be dispatched (unparseable
    constraints). Operational dead weight.
  Prove non-load-bearing: rebirth uses the proper pipeline -
    /archive + /birth - both emit audit events. Identity
    invariant preserved.
  Prove alternative is better:
    - Archive-only drops names operator wants kept.
    - Repair-YAML violates identity invariant.
    - Leave-in-quarantine is zombie surface area.
    Rebirth uniquely preserves identity + name + restores
    operational state.

After this lands:
  - section-05 reports the 3 new instance_ids as PASS.
  - audit chain has 6 new entries (3 archived + 3 created).
  - quarantine manifest ready for any future event."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 376 complete - quarantine resolved ==="
echo "=========================================================="
echo "Re-test: dev-tools/diagnostic/section-05-agent-inventory.command"
echo "Expected: new instance ids surface as PASS; old ones absent."
echo ""
echo "Press any key to close."
read -n 1 || true
