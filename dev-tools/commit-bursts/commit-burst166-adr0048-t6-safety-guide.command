#!/bin/bash
# Burst 166 — ADR-0048 T6 — operator safety guide runbook.
# Closes ADR-0048 fully (all six tranches shipped: T1 B159, T2
# B163, T3 B164, T4 B165, T5 B160, T6 here).
#
# What ships:
#
#   docs/runbooks/computer-control-safety.md (~280 lines):
#     Operator-facing companion to ADR-0048 covering practical
#     "how do I use this safely" questions:
#
#     - What the plugin does + DOESN'T do (six tools, each with
#       side-effect class + per-call defenses; explicit list of
#       things outside scope)
#     - macOS permissions you'll hit: Screen Recording (for
#       screencapture) + Accessibility (for click + type), with
#       which process to grant them to
#     - The three allowance presets walked through case-by-case
#       (Restricted / Specific / Full) — what each one means in
#       practice, when to pick it, default recommendation
#     - Posture as the global brake, with the matrix from ADR-0048
#       Decision 4 + the "red preserves grant state" semantic
#     - Audit-chain forensics: jq one-liners for "what did the
#       assistant do today," "when did I flip posture," "did
#       anyone change allowances"
#     - Threat model: prompt injection bounded by per-tool
#       defenses + posture + audit chain. Things outside the
#       threat model (operator misconfiguration, macOS-binary
#       vulns, an operator who approves a clearly malicious
#       prompt — read the args)
#     - Common scenarios: "read my screen but never click,"
#       "running a sensitive command, freeze the assistant,"
#       "the assistant clicked somewhere weird, what was it,"
#       "clean up screenshots," "revoke the assistant entirely"
#     - Quick-reference card for one-glance recall
#     - Cross-references to ADR-0047, ADR-0048, ADR-0045,
#       ADR-0043, ADR-0019, ADR-0005
#
#   docs/decisions/ADR-0048-computer-control-allowance.md:
#     T6 row in tranche table marked DONE B166. With T1+T2+T3+T4+T5
#     all shipped, this closes ADR-0048 implementation entirely.
#
# Per ADR-0048 Decision 1: zero kernel ABI surface changes (this
# is a documentation-only commit).
#
# Verification:
#   - The runbook renders cleanly as markdown (manual visual
#     inspection)
#   - All cross-references resolve (every ADR cited exists)
#   - Quick-reference card matches the actual UI semantics shipped
#     in B158 (settings panel) + B165 (allowance UI)
#
# ADR-0048 IMPL COMPLETE. Six tranches, six bursts:
#   T1 B159 — plugin scaffold
#   T2 B163 — read tools (screenshot + read_clipboard)
#   T3 B164 — action tools (click + type + run_app + launch_url)
#   T4 B165 — allowance UI (three presets + Advanced disclosure)
#   T5 B160 — posture clamp coverage (existing substrate confirmed)
#   T6 B166 — safety guide runbook (this commit)
#
# What's left in the broader assistant arc:
#   - ADR-0047 T4 allowances pane future substrate work: per-tool
#     grants would need a schema migration (plugin_grants → either
#     a tool_name column or a new plugin_tool_grants table). Not
#     gated on by anything else; can drive when needed.
#   - ADR-0052 T1-T6 implementation (~4-5 bursts) — pluggable
#     secrets storage. Design done B162; implementation queued.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/runbooks/computer-control-safety.md \
        docs/decisions/ADR-0048-computer-control-allowance.md \
        dev-tools/commit-bursts/commit-burst166-adr0048-t6-safety-guide.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(runbook): ADR-0048 T6 — computer-control safety guide (B166)

Burst 166. Closes ADR-0048 T6 and ADR-0048 entirely. The full
six-tranche implementation arc is shipped:

- T1 B159 plugin scaffold
- T2 B163 read tools (screenshot + read_clipboard)
- T3 B164 action tools (click + type + run_app + launch_url)
- T4 B165 allowance UI (three presets + Advanced)
- T5 B160 posture clamp coverage
- T6 B166 safety guide runbook (this commit)

Ships docs/runbooks/computer-control-safety.md — operator-facing
companion to the technical ADR. Practical 'how do I use this
safely' coverage:

- What the plugin does + DOESN'T do (six tools with per-tool
  defenses + explicit out-of-scope list)
- macOS permissions (Screen Recording + Accessibility) and
  which process to grant them to
- Three allowance presets walked through case-by-case with
  default recommendations
- Posture as the global brake (red preserves grant state;
  flipping back to green resumes without re-issuing)
- Audit-chain forensics with jq one-liners for the common
  questions ('what did the assistant do today,' 'when did I
  flip posture,' 'did anyone change allowances')
- Threat model: prompt injection bounded by per-tool defenses
  (script-injection-proof osascript wrappers, scheme allowlist
  on launch_url, app_name path-rejection on run_app, 4000-char
  cap on type) + posture + audit chain. Plus the things
  explicitly OUT of the threat model (operator misconfiguration,
  macOS-binary vulns, approving a clearly malicious prompt)
- Common scenarios with concrete preset+posture choices
- Quick-reference card for one-glance recall

ADR-0048 tranche table T6 marked DONE B166. Documentation-only;
zero kernel ABI surface changes per Decision 1.

ADR-0048 implementation arc closed. Remaining items in the broader
assistant work:
- Per-tool grant substrate (would unlock per-tool toggles in
  ADR-0048 T4 Advanced disclosure; needs schema migration; not
  gated on by anything)
- ADR-0052 T1-T6 implementation (pluggable secrets storage;
  design done B162; ~4-5 bursts queued)"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 166 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
