#!/bin/bash
# Burst 217 — ADR-0054 T6 safety guide runbook.
#
# T6 has two halves per the ADR's implementation tranches table:
# "Chat-tab card to review/delete shortcuts" + "operator runbook
# at docs/runbooks/procedural-shortcuts.md". B217 ships the
# runbook half. The chat-tab card is left for a follow-up burst
# since it's frontend work (new router endpoints + a Chat-tab
# subsection module) rather than docs.
#
# The runbook covers:
#   - What the substrate does + why operator-discoverable
#   - The three failure modes that make it default-off:
#     false matches, stale reinforcement, out-of-band emergent
#     behavior
#   - Master switch + sub-knob settings
#   - What lands in the audit chain (tool_call_shortcut shape)
#   - Reinforcement model (memory_tag_outcome.v1 +
#     memory_forget_shortcut.v1)
#   - Direct sqlite3 query to inspect the table until the
#     chat-tab card lands
#   - When to disable (model swaps, compliance, debugging)
#   - Failure-mode escalation (5-step playbook for a false-match
#     incident)
#   - What's still queued (per-agent thresholds, row aging)
#
# After this burst, an operator can confidently flip the master
# switch on with informed consent about the failure modes. The
# substrate is unblocked from a documentation perspective.
#
# What we deliberately did NOT do:
#   - Flip FSF_PROCEDURAL_SHORTCUT_ENABLED to true by default.
#     Default-off is the safety-conservative posture per ADR-0054
#     D2. Flipping it is an operator decision, not a code change.
#   - Build the chat-tab review card. That's separate frontend
#     work; queueing as a future burst.
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: pure documentation addition.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/runbooks/procedural-shortcuts.md \
        dev-tools/commit-bursts/commit-burst217-procedural-shortcut-runbook.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(adr-0054): T6 safety-guide runbook for procedural shortcuts (B217)

Burst 217. ADR-0054 T6 has two halves — chat-tab review card +
operator runbook. B217 ships the runbook so the substrate is
unblocked from a documentation perspective; the chat-tab card is
follow-up frontend work.

The runbook covers:
- What the substrate does + why default-off
- Three failure modes (false matches, stale reinforcement,
  out-of-band emergent behavior)
- Master switch + sub-knob settings with conservative defaults
- tool_call_shortcut audit event shape
- Reinforcement via memory_tag_outcome.v1 +
  memory_forget_shortcut.v1
- Direct sqlite3 query to inspect the table
- When to disable
- 5-step failure-mode escalation playbook

After this an operator can flip the master switch with informed
consent about the failure modes. Default stays off per ADR-0054 D2.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: pure documentation addition."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 217 complete ==="
echo "=== ADR-0054 T6 docs half shipped; substrate operator-unblocked. ==="
echo "Press any key to close."
read -n 1
