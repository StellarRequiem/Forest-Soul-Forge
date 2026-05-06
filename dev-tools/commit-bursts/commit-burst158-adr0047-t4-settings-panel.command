#!/bin/bash
# Burst 158 — ADR-0047 T4 (partial) — settings panel inside the
# Persistent Assistant pane.
#
# Closes T4 partially. Three of the four sub-cards from ADR-0047
# Decision 5 ship against existing kernel substrate:
#
#   1. Identity card  — GET /agents/{id}              (existing)
#   2. Posture dial   — GET/POST /agents/{id}/posture (ADR-0045)
#   3. Memory consents — GET /agents/{id}/memory/consents (ADR-0027)
#
# The fourth (computer-control allowances) stubs with copy pointing
# at ADR-0048 — that arc is design-only as of B146; implementation
# is its own multi-burst arc. Same partial-ship pattern as B147 used
# for T1.
#
# What ships:
#
#   frontend/index.html — adds <details id="chat-assistant-settings">
#     between the strip and the turns list. Four <section>s inside:
#     identity (dl with name/role/genre/dna/cons-hash/created),
#     posture (3 buttons + current indicator), consents (rendered
#     by JS), allowances (static placeholder copy referencing
#     ADR-0048).
#
#   frontend/js/chat.js:
#     ASSISTANT_SETTINGS_OPEN_KEY constant — persists the
#       <details>'s open/closed state across reloads.
#     loadAssistantSettings(instanceId) — calls Promise.allSettled
#       on three rendererers; one card's failure doesn't block the
#       others.
#     renderAssistantIdentity(instanceId) — populates the dl from
#       /agents/{id} response. Truncates DNA + constitution_hash to
#       16 chars + ellipsis for readability.
#     renderAssistantPosture(instanceId) — shows current posture +
#       highlights active button via .chat-assistant-posture-btn--active.
#     wireAssistantPostureButtons(instanceId) — idempotent; on click,
#       window.prompt for an audit reason, then writeCall to
#       POST /agents/{id}/posture. Toast on success/error.
#     renderAssistantConsents(instanceId) — fetches /agents/{id}/
#       memory/consents, filters revoked, renders as compact rows.
#       Empty state copy: "No active consent grants. The assistant's
#       memory stays private to itself."
#     loadAssistantConversation now also kicks off
#       loadAssistantSettings(instanceId).catch(() => {}) — best-
#       effort; chat surface never blocks on a settings hiccup.
#
#   frontend/css/style.css:
#     .chat-assistant-settings (collapsible block, custom marker),
#     .chat-assistant-settings__body (padded card stack),
#     .chat-assistant-card (per-section divider + spacing),
#     .chat-assistant-card__title + __hint (header style),
#     .chat-assistant-identity (96px label / 1fr value grid),
#     .chat-assistant-posture-row (button row layout),
#     .chat-assistant-posture-btn--active (active outline highlight),
#     .chat-assistant-consent-row (compact monospaced row).
#
# All endpoints used pre-existed; no daemon code touched. Per ADR-0047
# Decision 1 (userspace-only), zero kernel ABI surfaces touched.
#
# Verification:
#   - JS parse OK (node --check)
#   - HTML parse OK (html.parser)
#   - Browser refresh: Assistant tab → bound state → click "Settings"
#     → all four cards render. Identity shows the assistant agent's
#     stable instance/role/dna. Posture shows current dial; clicking
#     a different posture prompts for reason → writes to audit chain
#     → re-renders with new active highlight. Consents shows real
#     grants (or empty-state copy). Allowances shows the ADR-0048
#     pending stub.
#   - Open/closed state of the <details> persists across reloads
#     via localStorage (ASSISTANT_SETTINGS_OPEN_KEY).
#   - All three card loads are independent — kill the daemon's
#     consents endpoint and the other two still render.
#
# Closes ADR-0047 T4 (partial — allowances pane awaits ADR-0048).
# All 6 ADR-0047 implementation tranches shipped at the substrate
# level achievable today; full T4 closes when ADR-0048
# implementation lands.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add frontend/index.html \
        frontend/js/chat.js \
        frontend/css/style.css \
        dev-tools/commit-bursts/commit-burst158-adr0047-t4-settings-panel.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(chat): ADR-0047 T4 — assistant settings panel (B158)

Burst 158. Closes ADR-0047 T4 partially. Three of the four sub-cards
from Decision 5 ship against existing kernel substrate:
- Identity card (GET /agents/{id})
- Posture dial (GET/POST /agents/{id}/posture, ADR-0045)
- Memory consents (GET /agents/{id}/memory/consents, ADR-0027)

The fourth (computer-control allowances) stubs with copy referencing
ADR-0048 — that arc is design-only as of B146; implementation is
its own multi-burst arc.

Ships (frontend-only, per ADR-0047 Decision 1):
- index.html: <details id='chat-assistant-settings'> between strip
  and turns list; four <section> cards inside.
- chat.js: ASSISTANT_SETTINGS_OPEN_KEY (persist open state),
  loadAssistantSettings (Promise.allSettled across three renderers),
  renderAssistantIdentity, renderAssistantPosture +
  wireAssistantPostureButtons (window.prompt for audit reason +
  POST), renderAssistantConsents. Settings load is best-effort;
  chat surface never blocks on it.
- style.css: settings layout (collapsible block + card stack),
  identity dl grid, posture row, active-posture outline highlight.

All endpoints used pre-existed; zero daemon code touched, zero
kernel ABI surfaces touched.

Verification: JS + HTML parse clean; manual exercise of the surface
on a daemon with a bound assistant lands all 4 cards; posture flip
hits the audit chain; <details> open-state persists across reloads.

Closes ADR-0047 T4 partial. All 6 ADR-0047 tranches now shipped at
the substrate level achievable today. Full T4 closes when ADR-0048
implementation lands the per-(agent, plugin) computer-control grants."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 158 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
