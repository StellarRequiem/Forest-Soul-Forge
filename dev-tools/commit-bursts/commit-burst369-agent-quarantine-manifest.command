#!/bin/bash
# Burst 369 - agent quarantine manifest + section-05 awareness.
#
# Bug shape (surfaced by diagnostic-all on 2026-05-17):
#   section-05-agent-inventory FAIL on three active agents:
#     Kraine (system_architect_054edc592917)
#     Victor (knowledge_consolidator_9dd33078e7bd)
#     chaz   (software_engineer_871a237714a1)
#   All three carry a manually-appended free-text 'override' block
#   at EOF of their constitution YAML:
#     # --- override ---
#     you are the first version of a personal companion...
#   The text isn't a YAML key:value pair so the parser hits
#   'could not find expected colon, while scanning a simple key.'
#
# Why NOT rewrite the YAML:
#   CLAUDE.md architectural invariant: 'Constitution hash is
#   immutable per agent. A born agent's constitution hash is
#   bound to its identity; recomputing it invalidates verification.'
#   Repairing the YAML (commenting out the prose, OR wrapping it
#   in an override: | block) would change constitution_hash and
#   break audit chain integrity for every entry referencing the
#   old hash. Operator decision required for: archive these zombie
#   agents OR rebirth via the proper pipeline (which mints new
#   instance_ids and lets the audit trail show lineage).
#
# Fix shape (minimum-disturbance quarantine):
#
#   config/agent_quarantine.yaml (NEW):
#     Lists each broken-constitution agent with instance_id,
#     constitution_path, reason, intended_resolution, observed-date.
#     The manifest IS the paper trail. Lifecycle: operator decides
#     -> resolves via the proper pipeline -> removes the entry.
#     Three entries pre-seeded for Kraine/Victor/chaz.
#
#   section-05-agent-inventory.command:
#     Loads agent_quarantine.yaml into a QUARANTINE dict on startup.
#     Constitution parse failures for quarantined instance_ids
#     land as INFO (with the operator-supplied reason) instead of
#     FAIL. Untracked parse failures still FAIL — the harness keeps
#     surfacing genuinely new broken state.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: 3 FAILs daily on agents the operator already knows
#     about; pollutes drift detection.
#   Prove non-load-bearing: the YAML files are NOT touched -
#     identity hashes preserved. The probe gains a quarantine-aware
#     classification layer; untracked failures still FAIL.
#   Prove alternative is strictly better: alternatives are
#     (1) rewrite YAML = identity violation, no.
#     (2) blanket suppress parse failures = loses visibility on
#         genuinely new broken state, no.
#     (3) leave in place = false-positive churn, no.
#     Quarantine list is the only option that preserves identity
#     AND visibility AND signal-to-noise.
#
# Verification after this commit lands:
#   1. Re-run section-05-agent-inventory.command - Kraine/Victor/
#      chaz now report as INFO with the quarantine reason inline.
#   2. The remaining section-05 PASSes are unchanged.
#   3. If a NEW agent develops a constitution parse failure post-
#      B369, it still surfaces as FAIL until the operator adds it
#      to agent_quarantine.yaml.
#
# Operator next steps for the 3 quarantined agents:
#   - Read each constitution file at the path listed in the
#     manifest.
#   - Decide: archive (no longer used) or rebirth (still wanted,
#     rebuild from a clean schema).
#   - Use /archive endpoint or birth pipeline as appropriate.
#   - Remove the corresponding agent_quarantine.yaml entry once
#     the resolution lands.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add config/agent_quarantine.yaml \
        dev-tools/diagnostic/section-05-agent-inventory.command \
        dev-tools/commit-bursts/commit-burst369-agent-quarantine-manifest.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(harness): agent quarantine manifest (B369)

Burst 369. Close the section-05 FAILs on Kraine/Victor/chaz
without violating constitution-hash immutability.

Three active agents born 2026-05-07 carry a manually-appended
free-text 'override' block at constitution EOF that fails YAML
parse. Per CLAUDE.md ('Constitution hash is immutable per agent')
we MUST NOT rewrite the YAML to make it parse - that would change
identity and break audit chain integrity for every entry
referencing the old hash.

Fix shape (minimum-disturbance quarantine):

config/agent_quarantine.yaml (NEW):
  Lists each broken-constitution agent with instance_id,
  constitution_path, reason, intended_resolution, observed-date.
  The manifest IS the paper trail. Three entries pre-seeded.

section-05-agent-inventory.command:
  Loads agent_quarantine.yaml at startup. Constitution parse
  failures for quarantined instance_ids land as INFO (with the
  reason inline) not FAIL. Untracked failures still FAIL -
  genuinely new broken state still surfaces.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: 3 FAILs daily on already-known state.
  Prove non-load-bearing: YAML files NOT touched - identity hashes
    preserved. Untracked failures still FAIL.
  Prove alternative is better: alternatives (rewrite YAML /
    blanket suppress / leave in place) all violate either
    identity or visibility. Quarantine preserves both.

Operator next steps for the 3 quarantined agents:
  - Read each constitution file.
  - Decide archive vs. rebirth via the proper pipeline.
  - Remove the quarantine entry once resolved.

After this lands: section-05 drops 3 FAILs to INFO; the count
of genuine FAILs for the section becomes 0 (the Translator
Sandbox FAIL was already cleared by B368)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 369 complete - agent quarantine manifest ==="
echo "=========================================================="
echo "Re-test: dev-tools/diagnostic/section-05-agent-inventory.command"
echo "Expected: Kraine/Victor/chaz now INFO (with reason);"
echo "no new FAILs introduced."
echo ""
echo "Operator todo: review the 3 quarantined agents and decide"
echo "archive vs. rebirth. Manifest is the paper trail."
echo ""
echo "Press any key to close."
read -n 1 || true
