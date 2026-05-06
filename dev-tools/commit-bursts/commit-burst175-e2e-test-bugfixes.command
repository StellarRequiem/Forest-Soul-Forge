#!/bin/bash
# Burst 175 — two bugfixes surfaced during the live e2e test of
# the ADR-0047 + ADR-0048 + ADR-0052 surface (2026-05-06).
# Both were silent design-tier defects that wouldn't have shown
# up until an operator clicked through the assistant chat path
# end-to-end. Catching them at e2e is exactly what the test was
# for; landing the fixes restores the surface to working order.
#
# Bug #1 — CSS specificity bleed in the chat tab
#
#   Symptom: when an operator clicked Assistant mode, the rooms
#   pane (Rooms tab content) rendered ALONGSIDE the assistant
#   pane below it. Two distinct surfaces visible at once.
#
#   Root cause: B147 (chat mode toggle) wraps the rooms grid in
#   <div id="chat-pane-rooms" class="chat-grid">. The
#   showChatMode() handler sets `roomsPane.hidden = true` when
#   Assistant mode activates. But `.chat-grid { display: grid }`
#   in CSS has higher specificity (class) than the user-agent
#   `[hidden] { display: none }` rule (attribute). Class wins,
#   the pane stays visible.
#
#   Fix: add `.chat-grid[hidden] { display: none !important }`
#   with combined-selector specificity that beats `.chat-grid`
#   alone. Comment in CSS documents the chain so a future
#   refactor doesn't lose the !important.
#
# Bug #2 — trust_tier value mismatch
#
#   Symptom: clicking Specific or Full preset in the Computer-
#   control allowances row produced a 422 error from the daemon:
#       "trust_tier": "String should match pattern '^(green|yellow|red)$'"
#
#   Root cause: B165 (allowance UI) wrote "standard" / "elevated"
#   as the trust_tier values, derived from the operator-facing
#   ADR-0048 Decision 3 amendment text. But the GrantRequest
#   Pydantic model in plugin_grants.py validates trust_tier
#   against the same green/yellow/red regex as posture (per
#   ADR-0045 T3 substrate, B115). The substrate uses the
#   traffic-light dial; the ADR text described the abstraction
#   in operator terms. Frontend should have followed the
#   substrate.
#
#   Fix: chat.js applyAssistantAllowancePreset() now sends
#   trust_tier='green' for Full and 'yellow' for Specific.
#   Restricted still issues a DELETE (no grant). The
#   renderAssistantAllowances() inverse mapping (trust_tier →
#   preset) updated to match.
#
# Both fixes verified live via the e2e test:
#   - B175.1: assistant pane renders alone after the CSS change
#   - B175.2: clicking Specific produced 200 OK with grant
#     row {trust_tier: "yellow", granted_at_seq: 1773}
#   - Subsequent send-message round-trip succeeded: Sage replied
#     in 2.5s on qwen2.5-coder:7b, audit chain advanced 1773 to
#     1779 (6 events covering operator turn + memory_recall
#     dispatch + reply)
#
# What ships:
#
#   frontend/css/style.css:
#     +1 rule (.chat-grid[hidden]) with explanatory comment
#     pointing at this burst.
#
#   frontend/js/chat.js:
#     applyAssistantAllowancePreset() — trust_tier mapping
#     fixed; comment documents the substrate-vs-ADR-text gap so
#     a future contributor doesn't reintroduce the confusion.
#     renderAssistantAllowances() — inverse mapping updated to
#     read green=full, anything else (including stale rows)=specific.
#
#   dev-tools/restart-daemon.command (NEW):
#     Single-purpose daemon kickstart wrapper used during the
#     e2e test. launchctl kickstart -k + healthz wait + B173
#     endpoint probe. Less side-effect-heavy than
#     fix-bug1-restart-and-reset (which also resets scheduled
#     tasks); useful any time an operator needs to verify the
#     latest code is loaded.
#
# No tests added — the fixes are CSS-spec and value-mapping
# corrections that the existing e2e workflow catches the same
# way it surfaced them. A unit test for "frontend writes a
# value the backend schema accepts" would just couple the two
# layers tightly without adding signal beyond the e2e exercise.
#
# Per ADR-0044 D3: zero kernel ABI surface changes. Frontend +
# CSS only.
#
# Verification: e2e walk-through completed cleanly post-fixes.
# Sage assistant born + greeted + replied. Audit chain captured
# every turn. Settings panel renders all five cards (identity,
# posture, consents, plugin secrets, allowances).
#
# Outstanding gap (NOT a bug — design honesty): the soulux-
# computer-control plugin isn't installed in the operator's
# active plugin list yet. Granting it via the chat tab succeeds
# (the grant row lands in plugin_grants), but the assistant
# can't actually invoke its tools until `fsf plugin install` is
# run against examples/plugins/soulux-computer-control/. That's
# expected behavior — grants don't auto-install plugins.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add frontend/css/style.css \
        frontend/js/chat.js \
        dev-tools/restart-daemon.command \
        dev-tools/commit-bursts/commit-burst175-e2e-test-bugfixes.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(chat): two e2e-test bugfixes (B175)

Burst 175. Two silent design-tier defects surfaced during the
live e2e walk-through of the ADR-0047 + ADR-0048 + ADR-0052
surface on 2026-05-06.

Bug 1 — CSS specificity. .chat-grid display:grid outranks the
[hidden] UA rule because class beats attribute selector. Result:
the rooms pane stayed visible alongside the assistant pane in
Assistant mode. Fix: add .chat-grid[hidden] with combined-
selector specificity that wins, !important to seal it.

Bug 2 — trust_tier value mismatch. B165 wrote standard/elevated
as the grant trust_tier, derived from the ADR-0048 Decision 3
amendment text. But GrantRequest validates against the same
green/yellow/red regex as posture (ADR-0045 T3 substrate). 422
on every grant. Fix: applyAssistantAllowancePreset sends
trust_tier=green for Full and yellow for Specific; Restricted
still revokes. Inverse mapping in renderAssistantAllowances
updated.

Both verified live: assistant pane renders alone; clicking
Specific produced grant row at granted_at_seq 1773; subsequent
chat round-trip succeeded (Sage replied in 2.5s on
qwen2.5-coder:7b; audit chain advanced 1773 to 1779).

Also ships dev-tools/restart-daemon.command — single-purpose
kickstart wrapper used during the e2e test. Less side-effect-
heavy than fix-bug1-restart-and-reset.

Per ADR-0044 D3: zero kernel ABI surface changes. Frontend +
CSS + dev-tools only."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 175 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
