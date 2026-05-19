#!/bin/bash
# Burst 397 - ADR-0081 T4: wiring_audit.v1 signature skill.
#
# Fourth implementation tranche. The skill the WiringSentinel
# dispatches on the 4-hour cadence (T5) against section-15's
# coverage.json output.
#
# What this commit adds:
#
# 1. examples/skills/wiring_audit.v1.yaml — 4-step skill:
#      verify_chain: audit_chain_verify.v1 walks the chain. Per
#        ADR-0081 require_chain_verify_before_audit policy, a
#        broken chain aborts the audit.
#      recall_prior: memory_recall.v1 pulls prior wiring_audit
#        outcomes from lineage so the summary can diff (new
#        gaps, resolved gaps, persisting gaps).
#      summarize: text_summarize.v1 compacts the coverage block
#        into operator-readable severity-tagged punch list.
#        Severity mapping in the prompt: high (chain break or
#        parse failure or no-entry-agent handoff), medium
#        (orphan tools / unresolvable / broken handoffs),
#        low (kit-only tools), info (all green).
#      record: memory_write.v1 persists the outcome
#        (chain_ok, coverage_summary, summary_text) to lineage
#        memory tagged wiring_audit + wiring_sentinel.
#
#    Inputs:
#      coverage (required): full coverage.json from section-15.
#      severity_threshold (default medium): minimum severity
#        that surfaces as a delegate-queued escalation. The
#        T5 wrapper reads the outcome and decides.
#      triggered_by (default scheduled): scheduled | manual |
#        post-burst — operator-visible context.
#
#    Outputs: chain_ok, chain_entries_checked, orphan_tools_count,
#    handoffs_broken_count, skills_unresolvable_count,
#    summary_text, prior_audits_recalled. The T5 launchd
#    wrapper reads these to decide whether to log a notification.
#
# Why the skill takes coverage as an input rather than reading
# disk itself: skills run inside the daemon and dispatch tools;
# no read_file tool exists in the wiring_sentinel kit (and adding
# one would violate the guardian read_only ceiling rule that
# kits can't include filesystem-reach tools). The T5 wrapper
# shell script reads coverage.json from disk and embeds it in
# the skill dispatch payload. This keeps the skill stateless
# and the sentinel kit minimal.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: T3 birthed the sentinel but it has no signature
#     skill yet. Without T4 the sentinel can't actually audit
#     anything; it's a dead role until T4 lands.
#   Prove non-load-bearing: skill manifest ADDITION only. No
#     substrate or pipeline change. The skill is only dispatched
#     by the T5 wrapper; T5 is gated on its own commit.
#   Prove alternative is strictly better:
#     (a) collapse audit into a single tool call (no skill needed)
#         - loses the chain-verify-first discipline + lineage
#         memory write. The skill engine's step-by-step audit
#         attribution is what makes regressions traceable.
#     (b) bake audit into a bash script (no agent, no skill) -
#         loses agent identity in the audit chain. Without an
#         instance_id attribution, the audit-chain forensics
#         can't tell which sentinel run produced which finding.
#
# Verification after this commit lands:
#   1. python3 -c \"import yaml; yaml.safe_load(open('examples/skills/wiring_audit.v1.yaml'))\"
#      Expected: no exception.
#   2. bash dev-tools/diagnostic/section-02-skill-manifests.command
#      Expected: wiring_audit.v1 in the inventory, all requires
#      resolve.
#   3. force-restart-daemon to load the new skill.
#   4. (After T3 lands) dispatch the skill manually:
#      curl -X POST http://127.0.0.1:7423/agents/<sentinel_id>/skills/call \
#        -H \"X-FSF-Token: \$FSF_API_TOKEN\" \\
#        -d '{\"skill_name\":\"wiring_audit\",\"skill_version\":\"1\",\"session_id\":\"manual-test\",\"inputs\":{\"coverage\":{...}}}'
#      Expected: ok=true, summary_text non-empty, lineage memory
#      gains one wiring_audit_outcome entry.
#
# What this UNBLOCKS / queues next:
#   T5: scheduled task + runbook (the launchd wrapper that
#       feeds coverage.json into this skill on a 4-hour tick).
#   T6: CLOSE - live verify + north-star + Accepted.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add examples/skills/wiring_audit.v1.yaml \
        dev-tools/commit-bursts/commit-burst397-adr0081-t4-wiring-audit-skill.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(skill): wiring_audit.v1 signature skill (ADR-0081 T4, B397)

Burst 397. Fourth tranche of ADR-0081. The signature skill the
WiringSentinel (T3) dispatches on the 4-hour cadence (T5)
against section-15 (T1) coverage.json.

examples/skills/wiring_audit.v1.yaml:
  4-step skill:
    verify_chain - audit_chain_verify.v1 walks the chain. Per
      require_chain_verify_before_audit policy a broken chain
      aborts.
    recall_prior - memory_recall.v1 pulls prior outcomes so the
      summary can diff new/resolved/persisting gaps.
    summarize - text_summarize.v1 compacts coverage into
      operator-readable severity-tagged punch list. Severity
      mapping: high (chain break / parse fail / no-entry-agent
      handoff), medium (orphans / unresolvable / broken
      handoffs), low (kit-only tools), info (all green).
    record - memory_write.v1 persists outcome to lineage
      tagged wiring_audit + wiring_sentinel.

Inputs: coverage (required), severity_threshold (default
medium), triggered_by (default scheduled).
Outputs: chain_ok, *_count fields, summary_text,
prior_audits_recalled.

Coverage is an input (not read from disk inside the skill)
because skills run inside the daemon and dispatch tools; no
read_file tool in the wiring_sentinel kit (and adding one
would break the guardian read_only ceiling). The T5 wrapper
shell script reads coverage.json and embeds it in the dispatch
payload. Keeps skill stateless and sentinel kit minimal.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: T3 birthed sentinel but it has no signature skill
    yet. Sentinel is dead role until T4 lands.
  Prove non-load-bearing: manifest ADDITION only. Skill only
    dispatched by T5 wrapper which is gated on its own commit.
  Prove alternative is better: collapsing into a single tool
    call loses chain-verify discipline + lineage write;
    bash-script audit loses agent identity in the chain.

T5+T6 queued: scheduled task + runbook -> CLOSE."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 397 complete - ADR-0081 T4 shipped ==="
echo "=========================================================="
echo "Verify:"
echo "  bash dev-tools/diagnostic/section-02-skill-manifests.command"
echo "  Expected: wiring_audit.v1 in the inventory, requires resolve."
echo ""
echo "Next: T5 (scheduled task + runbook)."
echo ""
echo "Press any key to close."
read -n 1 || true
