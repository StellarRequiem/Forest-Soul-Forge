#!/bin/bash
# Burst 403 - ADR-0081 HOTFIX-4: wiring_audit memory_write content type.
#
# Surfaced on B402's re-verify: skill ran 3/4 steps successfully
# (verify_chain + recall_prior + summarize-with-full-LLM-output ~20s),
# then failed at step `record` because memory_write.v1's input_schema
# requires:
#
#   content: {type: string}
#
# T4 (B397) wrote `content` as a dict (kind/triggered_by/chain_ok/...),
# matching the chain_health_probe.v1 manifest's shape. But unlike
# chain_health_probe (which works via the skill engine's dict-arg
# compiler from ADR-0057), this manifest's dict had nested
# interpolations that didn't serialize cleanly.
#
# Safer + simpler: emit `content` as a colon-delimited summary
# string with the LLM summary appended. Same lineage receipt; the
# structured drilldown lives in coverage.json on disk anyway.
#
# What this commit adds:
#
# 1. examples/skills/wiring_audit.v1.yaml — record step's content
#    arg changes from dict to string. Includes the load-bearing
#    fields (triggered_by, chain_ok, chain_entries, orphan_tools,
#    handoffs_broken, skills_*_count) + the full LLM summary
#    appended.
#
# Operator sync step (gitignored install dir):
#   cp examples/skills/wiring_audit.v1.yaml data/forge/skills/installed/
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: skill status=failed at step `record`. The lineage
#     memory write that's supposed to provide tamper-evident
#     attribution for each audit never happens. Sentinel runs but
#     leaves no durable receipt.
#   Prove non-load-bearing: one yaml step's content arg shape.
#   Prove alternative: dict path requires deeper skill-engine
#     compiler debugging; string path is simpler + matches
#     memory_write.v1's schema literally + matches key_audit.v1's
#     pattern.
#
# Verification after this commit lands:
#   1. (already-synced installed dir from sandbox prior to commit)
#   2. bash dev-tools/run-wiring-audit.command
#      Expected: status=succeeded. output non-null. memory entry
#      created.
#   3. curl -s 'http://127.0.0.1:7423/agents/<sentinel_id>/memory?tags=wiring_audit&limit=5'
#      Expected: at least one entry with content starting
#      'wiring_audit_outcome:'.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add examples/skills/wiring_audit.v1.yaml \
        dev-tools/commit-bursts/commit-burst403-adr0081-hotfix-memory-write-content.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(skill): wiring_audit memory_write content type (ADR-0081 HOTFIX-4, B403)

Burst 403. HOTFIX-4. Skill ran 3/4 steps successfully (chain
verify + prior recall + ~20s LLM summarize), then failed at
record because memory_write.v1 schema requires content:string,
not the dict I authored in B397.

Fix: serialize record's content as colon-delimited summary
string. Load-bearing fields preserved (triggered_by, chain_ok,
chain_entries, orphan_tools, handoffs_broken, skills_*_count) +
full LLM summary appended. Structured drilldown stays in
coverage.json on disk; memory entry is the lineage receipt.

Operator sync (gitignored install dir):
  cp examples/skills/wiring_audit.v1.yaml data/forge/skills/installed/

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: skill ends in status=failed at record. No durable
    memory attribution for each audit run.
  Prove non-load-bearing: one yaml step's content arg.
  Prove alternative: dict path requires skill-engine compiler
    deep-debug; string matches schema literally + matches
    key_audit.v1's pattern."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 403 complete - ADR-0081 HOTFIX-4 shipped ==="
echo "=========================================================="
echo "Next: bash dev-tools/run-wiring-audit.command"
echo ""
echo "Press any key to close."
read -n 1 || true
