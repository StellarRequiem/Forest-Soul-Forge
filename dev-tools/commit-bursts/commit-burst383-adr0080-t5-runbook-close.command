#!/bin/bash
# Burst 383 - ADR-0080 T5: operator runbook + CLOSE ADR-0080.
#
# Closes the per-agent capability tree arc that opened with B374
# (proposal) and progressed through B380 (T1 backend) -> B381
# (T2 frontend) -> B382 (T3 toggle). T4 (inferred tool->tool edges)
# is deferred indefinitely; not needed for daily operator
# workflow today.
#
# Plus a queued follow-on: T3b adds the per-agent overrides table
# + runtime enforcement. The audit chain is the durable record
# in the meantime per B382's audit-first design.
#
# Files in this commit:
#
#   docs/runbooks/agent-capability-tree.md (NEW)
#     Operator runbook. Covers what the tab answers, how to read
#     the three states (live/broken/in_progress) + two binding
#     modes (hard_wired/operator_toggleable), how the toggle
#     works, when to use this tab vs. the global Tools/Skills
#     tabs, what the tab does NOT do, recovery paths (rebirth
#     for hard-wired changes, audit walk for toggle history,
#     missing-tool repair), cross-references to ADR-0080 + the
#     four bursts, and the verification checklist.
#
#   docs/decisions/ADR-0080-per-agent-capability-tree-ui.md (MOD)
#     Status: Proposed -> Accepted (4/5 tranches shipped; T4
#     deferred indefinitely). Closed-in line names all four
#     bursts that shipped + flags T4 as deferred + T3b as
#     queued.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm of NOT shipping T5: operators have working UX
#     but no documented surface to read when a teammate asks
#     "what does the Capabilities tab DO?" or "why can't I
#     toggle this tool?" The tab itself surfaces the binding
#     glyphs but not the deeper context (rebirth path, audit-
#     first rationale, T3b enforcement gap).
#   Prove non-load-bearing: doc-only commit. No substrate or
#     frontend change. No new tests.
#   Prove alternative is strictly better: keep-as-tribal-
#     knowledge loses fidelity across sessions; chat-only is
#     ephemeral.
#
# What this UNBLOCKS / CLOSES:
#   ADR-0080 arc closed. Operator has runbook for daily use.
#   T3b is the natural next burst for the capability arc when
#   runtime enforcement becomes a felt need (today the audit
#   trail is sufficient).
#
#   With this commit ADR-0080's queue moves from "Proposed +
#   5 tranches outstanding" to "Accepted + T3b queued + T4
#   deferred".
#
# Verification after this commit lands:
#   1. Read docs/runbooks/agent-capability-tree.md end-to-end.
#   2. Read docs/decisions/ADR-0080-per-agent-capability-tree-ui.md
#      and confirm Status: Accepted block names all four
#      bursts that shipped.
#   3. Substrate is unchanged; no daemon restart needed.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/runbooks/agent-capability-tree.md \
        docs/decisions/ADR-0080-per-agent-capability-tree-ui.md \
        dev-tools/commit-bursts/commit-burst383-adr0080-t5-runbook-close.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(adr): ADR-0080 T5 runbook + CLOSE (B383)

Burst 383. Closes ADR-0080 (per-agent capability tree UI).

docs/runbooks/agent-capability-tree.md (NEW):
  Operator runbook. What the tab answers; how to read three
  states (live/broken/in_progress) + two bindings (hard_wired
  /operator_toggleable); how the toggle works; when to use
  this tab vs. Tool Registry/Skills; what the tab does NOT
  do; recovery paths (rebirth for hard-wired changes, audit
  walk for toggle history, missing-tool repair); cross-refs
  to ADR + bursts; verification checklist.

docs/decisions/ADR-0080-per-agent-capability-tree-ui.md (MOD):
  Status: Proposed -> Accepted. 4/5 tranches shipped:
    T1 B380 - backend GET endpoint
    T2 B381 - frontend Capabilities tab
    T3 B382 - toggle endpoint + audit event
    T4 (DEFERRED) - inferred tool->tool edges
    T5 B383 (this) - runbook + close
  T3b queued as follow-on: per-agent overrides table +
  runtime enforcement of toggles.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: operators have working UX but no documented
    surface for teammates / future sessions.
  Prove non-load-bearing: doc only.
  Prove alternative is better: tribal-knowledge loses fidelity
    across sessions.

After this lands:
  ADR-0080 arc closed.
  T3b (runtime enforcement) is the natural follow-on when
    audit-only stops being sufficient."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 383 complete - ADR-0080 CLOSED ==="
echo "=========================================================="
echo "Runbook: docs/runbooks/agent-capability-tree.md"
echo "ADR status: Accepted (T4 deferred, T3b queued)"
echo ""
echo "Press any key to close."
read -n 1 || true
