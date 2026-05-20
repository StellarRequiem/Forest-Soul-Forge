#!/usr/bin/env bash
# Burst 447 — README experimental + operator-responsibility
# disclaimer + positioning decision recorded in memory.
#
# Two artifacts:
#   1. README.md gets an experimental + responsibility disclaimer at
#      the very top, ahead of the existing developer-pointer and
#      strategic-posture blockquotes. Operators read this FIRST.
#      Body: experimental software; operator owns every agent
#      action (financial, legal, communicative, irreversible);
#      audit chain makes actions traceable but does not absolve
#      consequences; no warranty; ADR-0046 license link.
#
#   2. GitHub repo description updated from the placeholder
#      'Agentic runtime system still in progress' to a
#      Candidate-B positioning sentence:
#        'Local-first agent governance kernel — every agent has a
#         signed identity, a constitutional rulebook compiled from
#         operator-set traits, and a tamper-evident audit chain.
#         Experimental: operators run agents on their own hardware
#         and own every action those agents take.'
#      Edit driven through Chrome MCP; operator clicked Save.
#      That metadata change isn't tracked in git; this commit
#      records the decision context.
#
# Positioning decision recorded for next session:
#   The audience anchor is Candidate B — operator substrate. Aligns
#   with what FSF has actually been built for through today's 13
#   commits (Tier 1 hardening + Triune-Main + 6/6 launchd cadences
#   + capability tree UI + ADR-0023 quality batteries). The UI
#   refresh + Homebrew installer + landing-page work that the
#   operator queued earlier will be designed against this anchor:
#     UI: vertical sidebar grouped by operator workflow
#         (Build / Run / Observe / Marketplace / System).
#     Installer: Homebrew now + signed .pkg when Apple Dev ID
#         arrives Friday.
#     Landing: triune/cadence/governance story; screenshots of the
#         operator-facing UI; 'day in the life of an operator'
#         walkthrough.
#     GitHub release: tagged + release notes drawn from this day's
#         audit-doc bodies.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: README currently has zero disclaimer language;
#     a future operator (or external reader of the repo) gets no
#     up-front warning about experimental status + responsibility.
#     The repo description was a placeholder ('still in progress')
#     that conveyed nothing about what the project is.
#   Prove non-load-bearing for kernel: docs change + GitHub repo
#     metadata. No schema, no events, no routes, no code.
#   Prove alternative: bury the disclaimer in CONTRIBUTING (rejected;
#     README is the first read); skip the disclaimer entirely
#     (rejected; user explicitly requested it).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 447 — README disclaimer + positioning decision"
echo "==========================================================="
echo

echo "Pre-commit: clear stale .git locks..."
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null && echo "  cleared" || echo "  none"
echo

git add README.md
git add dev-tools/commit-bursts/commit-burst447-readme-disclaimer-and-positioning.command

echo "Staged:"
git diff --cached --stat
echo

git commit -m "docs(readme): experimental + operator-responsibility disclaimer + positioning anchor (B447)

Adds two surfaces of operator-facing clarity:

(1) README.md disclaimer (near top, ahead of every other section)
    Body:
      * 'Experimental software in active development; provided for
         research, experimentation, and personal use.'
      * 'The operator is responsible for every action committed by
         the agents they configure and run' — financial, legal,
         communicative, irreversible, or otherwise consequential.
      * 'Every tool invocation, skill execution, scheduled-task
         firing, and delegate-chain hop runs under the operator's
         identity and on the operator's hardware.'
      * 'The audit chain makes every action traceable; it does not
         absolve the operator of its consequences.'
      * 'No warranty is provided.'
      * Link to ADR-0046 (License + Governance) for licensing.

(2) GitHub repo description (out-of-tree metadata; recorded here)
    From: 'Agentic runtime system still in progress'  (placeholder)
    To:   'Local-first agent governance kernel — every agent has a
           signed identity, a constitutional rulebook compiled from
           operator-set traits, and a tamper-evident audit chain.
           Experimental: operators run agents on their own hardware
           and own every action those agents take.'
    Driven via Chrome MCP on the About-edit dialog. Operator
    clicked Save manually (security-control modification = not
    Claude's to click).

Positioning anchor decision (recorded for next session's UI /
installer / landing work):
  Audience anchor = Candidate B (operator substrate). Three
  candidates were brought to the operator (developer-first /
  operator-substrate / research-substrate). The operator picked
  B as the read that matches what FSF actually is today —
  the work of the last 13 commits has been operator-grade
  governance machinery, not developer SDK polish or research
  export tooling.

  This anchors subsequent work:
    UI refresh: vertical sidebar grouped by operator workflow
      (Build / Run / Observe / Marketplace / System).
    Installer: Homebrew now + signed .pkg when Apple Dev ID
      arrives Friday.
    Landing: triune/cadence/governance story; screenshots of the
      operator-facing UI; 'day in the life of an operator'
      walkthrough.
    GitHub release: tagged + release notes drawn from this day's
      audit-doc bodies.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: README had zero disclaimer; external reader gets
    no up-front warning about experimental status + agent-action
    responsibility. Repo description was a placeholder.
  Prove non-load-bearing for kernel: docs + GitHub metadata.
    No schema, no events, no routes, no code.
  Prove alternative: bury disclaimer in CONTRIBUTING (rejected);
    skip entirely (rejected; user requested it concretely)." || { echo "commit failed"; exit 1; }

echo
echo "Post-commit signature status:"
git log --format='%h %G? %s' -3
echo

echo "Pushing B447..."
git push origin main || { echo "push failed"; exit 1; }

echo
echo "Done. B447 pushed."
echo
echo "Press any key to close."
read -n 1 || true
