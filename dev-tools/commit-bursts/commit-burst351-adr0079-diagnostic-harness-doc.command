#!/bin/bash
# Burst 351 - ADR-0079 T1: diagnostic harness decision doc.
#
# Motivated by B350: audit_chain_verify.v1 had been declared a
# working tool since ADR-0033 Phase B1 but was actually dead code
# on the HTTP path. Unit tests passed because the test fixture
# constructs ToolContext by hand; the dispatcher never wired the
# chain in. The bug surfaced only when D3 Phase A's archive_
# evidence.v1 became the first real consumer.
#
# The B350 fix is single. The B350-class concern is structural —
# the dispatcher exposes 9 subsystems via ToolContext and a typo
# or missed wire on any silently kills the tools depending on it.
# Beyond ToolContext, the same "claimed working, actually dead"
# pattern can hit tool registration, skill manifests, handoff
# routing, encryption-at-rest paths, frontend tabs, etc.
#
# ADR-0079 lays out a section-by-section diagnostic harness: 13
# sections each with its own standalone .command driver, sequential,
# fail-loud-but-umbrella-runs-all-sections. Replaces the per-rollout
# live-test-<phase>.command proliferation with one harness that
# exercises the live runtime more fully. Runs operator-driven before
# Phase closures + release tags, not in CI (CI doesn't have a live
# daemon; pytest covers the unit surface; this covers the runtime
# wiring surface pytest can't reach).
#
# What ships:
#
# 1. docs/decisions/ADR-0079-diagnostic-harness.md (NEW):
#    Six decisions documented:
#      D1 — 13 sections, each one .command + one report.md.
#           Section catalog covers static config integrity (1-4) →
#           runtime wiring (5-7, the B350-class catch zone) →
#           system integrations (8-13).
#      D2 — Sequenced; later sections skip cleanly if a load-
#           bearing prior section fails.
#      D3 — Fail loud per section but umbrella runs ALL sections
#           so operator gets the full punch list.
#      D4 — Operator-driven cadence (before Phase closures + release
#           tags), NOT in CI.
#      D5 — Markdown summary report aggregating all sections.
#      D6 — Section-as-script, not section-as-library (avoids
#           import tangles; matches existing live-test-*.command
#           pattern).
#
# Tranches: T1 this burst + T2-T5 four long bursts (sections 1-4 /
# 5-7 / 8-10 / 11-13) + T6 umbrella + runbook. ~6 bursts to land
# the harness. After T6 the original ADR-0064 T3 (telemetry chain
# hookup) becomes the next direction with substrate health known.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0079-diagnostic-harness.md \
        dev-tools/commit-bursts/commit-burst351-adr0079-diagnostic-harness-doc.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(diagnostic): ADR-0079 - diagnostic harness decision doc (B351)

Burst 351. Motivated by B350: audit_chain_verify.v1 had been
declared working since ADR-0033 Phase B1 but was dead code on the
HTTP path. Unit tests passed (fixture builds ToolContext by hand);
dispatcher never wired the chain in. Surfaced only when D3 Phase A
archive_evidence.v1 became the first real consumer.

B350 fix was single line. B350-class concern is structural: the
dispatcher exposes 9 subsystems via ToolContext (memory, delegate,
priv_client, secrets, agent_registry, procedural_shortcuts,
personal_index, provider, audit_chain), each wired in one place;
a typo or missed wire silently kills dependent tools. Beyond
ToolContext, same claimed-working-but-actually-dead pattern can
hit tool registration, skill manifests, handoff routing,
encryption paths, frontend tabs.

ADR-0079 lays out a section-by-section diagnostic harness: 13
sections, each a standalone .command driver writing a structured
report.md, sequential, fail-loud-per-section but umbrella runs
all sections so the operator gets the full punch list.

Section catalog:
  01 static-config           06 ctx-wiring (B350-class catch)
  02 skill-manifests         07 skill-smoke
  03 boot-health             08 audit-chain-forensics
  04 tool-registration       09 handoff-routing
  05 agent-inventory         10 cross-domain-orchestration
                             11 memory-retention
                             12 encryption-at-rest
                             13 frontend-integration

Sequencing: numeric order; later sections skip cleanly if a
load-bearing prior failed. Frequency: operator-driven before
Phase closures + release tags. NOT in CI (CI lacks a live daemon;
pytest covers the unit surface; this covers the runtime-wiring
surface pytest cant reach).

Tranches: T1 this burst + T2-T5 four long bursts (sections 1-4 /
5-7 / 8-10 / 11-13) + T6 umbrella + runbook = ~6 bursts. After T6
ADR-0064 T3 (telemetry chain hookup) becomes next direction with
substrate health known."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 351 complete - ADR-0079 doc shipped ==="
echo "Next: B352 - T2 sections 01-04 (static-config + skill-manifests"
echo "+ boot-health + tool-registration). Long burst."
echo ""
echo "Press any key to close."
read -n 1 || true
