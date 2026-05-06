#!/bin/bash
# Burst 165 — ADR-0048 T4 — Allowance UI for the soulux-computer-
# control plugin. Closes ADR-0047 T4 fully (the allowances stub
# from B158 is now a working surface).
#
# Three preset buttons in the Chat-tab assistant settings panel:
#
#   Restricted → DELETE /agents/{id}/plugin-grants/<plugin>.
#                Revokes the grant. Assistant cannot fire any
#                computer-control tool. Existing grants get an
#                agent_plugin_revoked audit event.
#
#   Specific   → POST /agents/{id}/plugin-grants with
#                trust_tier='standard'. Plugin enters the agent's
#                effective allowlist; read tools fire freely;
#                action tools require per-call approval per the
#                manifest. Posture clamps still apply.
#
#   Full       → POST same endpoint with trust_tier='elevated'.
#                Same effect today as Specific because ADR-0045 T3
#                per-grant tier enforcement is forward-compat
#                substrate (`enforce_per_grant=False` default in
#                PostureGateStep). The trust_tier choice is
#                recorded so when T3 substrate flips on, the
#                operator's intent is preserved without
#                re-issuing.
#
# Honest scope: per-tool granularity in the Advanced disclosure
# awaits a substrate extension. Forest's plugin-grants API
# operates at PLUGIN scope today (one row per (agent, plugin),
# not per (agent, plugin, tool)). The Advanced disclosure ships
# as a read-only reference table showing each of the six tools'
# side_effects + approval-gate classification, with an inline
# explanation that posture provides the orthogonal "global brake"
# until per-tool grants land. This is the §0 Hippocratic-gate
# choice: ship what the substrate supports honestly rather than
# fake a per-tool UI that silently no-ops.
#
# What ships:
#
#   frontend/index.html — replaces the ADR-0048-pending stub copy
#     in #chat-assistant-ready's allowances card with:
#     - live grant-state status line (#chat-assistant-allow-status)
#     - three preset buttons (.chat-assistant-preset-row)
#     - <details> Advanced disclosure with the 6-tool reference
#       table (.chat-assistant-tool-table)
#     - per-action feedback line (#chat-assistant-allow-feedback)
#
#   frontend/js/chat.js:
#     - ALLOW_PLUGIN_NAME constant
#     - renderAssistantAllowances(instanceId) — fetches grants
#       via GET /agents/{id}/plugin-grants, finds the soulux-
#       computer-control row, derives the active preset
#       (no-grant→restricted; standard→specific; elevated→full),
#       paints the status line + active-button highlight (reuses
#       .chat-assistant-posture-btn--active for visual continuity)
#     - wireAssistantAllowanceButtons(instanceId) — idempotent
#       click handler; routes to applyAssistantAllowancePreset
#       which hits POST or DELETE depending on preset; toast on
#       success/error; re-render after each change
#     - loadAssistantSettings now adds renderAssistantAllowances
#       to the Promise.allSettled fan-out (fourth card, alongside
#       identity / posture / consents). Per-card failure stays
#       isolated — one card's API problem doesn't block the
#       others.
#
#   frontend/css/style.css:
#     - .chat-assistant-preset-row (button row layout)
#     - .chat-assistant-allow-advanced (collapsible details with
#       custom triangle marker)
#     - .chat-assistant-tool-table (compact reference table with
#       muted accents for the column headers)
#
#   docs/decisions/ADR-0048-computer-control-allowance.md:
#     T4 row in the tranche table marked DONE B165 (partial — the
#     per-tool granularity caveat). Closes ADR-0047 T4 fully.
#
# Per ADR-0048 Decision 1: zero kernel ABI surface changes.
# Per ADR-0044 D3: existing endpoints used as-shipped — no
# schema migration, no new event types.
#
# Verification:
#   - JS parse OK (node --check)
#   - HTML parse OK (html.parser)
#   - Manual exercise on a daemon with a bound assistant agent:
#     - Click Restricted → DELETE fires → audit chain shows
#       agent_plugin_revoked → status line flips to "not granted"
#     - Click Specific → POST fires with trust_tier=standard →
#       audit chain shows agent_plugin_granted → status line
#       shows "granted (tier: standard)" + Specific button
#       highlights
#     - Click Full → POST fires with trust_tier=elevated →
#       status line shows "granted (tier: elevated)" + Full
#       button highlights
#     - Reload → state persists (the GET on next entry shows
#       the same preset)
#
# Remaining ADR-0048 tranches:
#   T6 — Documentation + safety guide (`docs/runbooks/`)
#
# Future substrate work that would unlock per-tool granularity in
# T4's Advanced disclosure:
#   - Schema extension: plugin_grants table → plugin_tool_grants
#     table OR add a tool_name column to plugin_grants
#   - GrantRequest schema: add optional tool_name field
#   - Dispatcher: ApprovalGateStep consults per-tool grants
#     before falling back to the per-plugin grant
#   This is its own ADR + multi-tranche arc; not gated on by
#   ADR-0048 T4 partial-shipping.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add frontend/index.html \
        frontend/js/chat.js \
        frontend/css/style.css \
        docs/decisions/ADR-0048-computer-control-allowance.md \
        dev-tools/commit-bursts/commit-burst165-adr0048-t4-allowance-ui.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(chat): ADR-0048 T4 — allowance UI (B165)

Burst 165. Closes ADR-0048 T4 (partial) and ADR-0047 T4 fully.
The allowances stub in the Chat-tab assistant settings panel
is now a working three-preset surface wired to the existing
plugin-grants API.

Three presets:
- Restricted: DELETE the soulux-computer-control grant; assistant
  cannot fire any computer-control tool.
- Specific: POST grant with trust_tier='standard'. Plugin enters
  the agent's effective allowlist; read tools fire freely; action
  tools require per-call approval per the manifest. Posture
  clamps still apply.
- Full: POST grant with trust_tier='elevated'. Same effect today
  as Specific (ADR-0045 T3 per-grant-tier enforcement is forward-
  compat substrate); trust_tier intent recorded for when T3
  substrate flips on.

Honest scope: per-tool granularity in the Advanced disclosure
awaits a substrate extension. Forest's plugin-grants API is
plugin-scoped today (no per-tool rows). The Advanced disclosure
ships as a read-only reference table of all 6 tools with their
side_effects + approval classification, plus an inline note that
posture provides the orthogonal global brake until per-tool
grants land. §0 Hippocratic gate: ship what the substrate
supports honestly rather than fake a per-tool UI that silently
no-ops.

Ships (frontend-only, per ADR-0048 Decision 1):
- index.html: stub replaced with status line + preset buttons +
  Advanced disclosure + per-tool reference table
- chat.js: ALLOW_PLUGIN_NAME constant, renderAssistantAllowances
  + wireAssistantAllowanceButtons + applyAssistantAllowancePreset.
  Plugged into loadAssistantSettings's Promise.allSettled fan-out
  alongside identity/posture/consents.
- style.css: .chat-assistant-preset-row + .chat-assistant-allow-
  advanced + .chat-assistant-tool-table
- ADR-0048 tranche table: T4 marked DONE B165 with the partial
  caveat.

Per ADR-0048 Decision 1: zero kernel ABI surface changes. Existing
plugin-grants endpoints used as-shipped — no schema migration, no
new event types, no new endpoints.

Remaining ADR-0048 tranche: T6 (docs + safety guide).

Future substrate work (not gated on T4): schema extension to
plugin_tool_grants OR per-tool field on plugin_grants would
enable real per-tool granularity in the Advanced disclosure."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 165 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
