#!/bin/bash
# Burst 365 - section-01 + section-09: planned-domain status-aware
# entry_agent reference checks.
#
# Bug shape (surfaced by diagnostic-all on 2026-05-17):
#   section-01-static-config FAIL:
#     "d10_research_lab.yaml: entry_agent role 'gatherer' not in
#      trait_engine" (plus analyst, critic, synthesizer,
#      debate_moderator - five total)
#   section-09-handoff-routing FAIL:
#     "d10_research_lab: role 'gatherer' not in trait_engine"
#     (same five roles)
#
# This is expected substrate state, not a bug. ADR-0067 set the
# domain rollout dependency order (D4 -> D3 -> D8 -> D1 -> D2 ->
# D7 -> D9 -> D10 -> D5 -> D6). Each domain's role wiring lands
# when its arc begins (D4 wired its three roles in B331-B340; D3
# Phase A wired forensic_archivist in B342-B347). D10 is 8 arcs
# away, so its 5 deferred roles SHOULD be unlanded today.
#
# Pre-B365 the harness reported these as FAIL, which means every
# daily scheduled run flagged them as new drift. The baseline-
# aware drift detector exists exactly to filter this kind of
# expected-but-pre-rollout state. Better fix: make the harness
# itself aware of "status: planned" and treat unlanded entry_agents
# from planned domains as deferred (PASS with a deferred-list
# suffix) rather than FAIL.
#
# Two-file fix:
#
#   section-01-static-config.command:
#     The "every domain manifest loads + entry_agents reference
#     real roles" check now skips planned domains. A new sibling
#     check "planned domain manifests catalogued for upcoming
#     rollout arcs" enumerates each planned domain + its unlanded
#     roles - PASS by design. Operator gets a single read of
#     "what wiring is queued for which arc."
#
#   section-09-handoff-routing.command:
#     Check 2 (entry_agents reference real claimed roles) gains
#     the same planned-skip + deferred-suffix shape. Planned
#     domains' unlanded roles append to the PASS evidence string
#     so the visibility doesn't vanish.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: section-01 + section-09 FAIL today on intended
#     pre-rollout state, polluting the daily summary with five
#     known-fine items.
#   Prove non-load-bearing: the strict check still fires for
#     non-planned domains; planned domains' unlanded roles still
#     surface (just as PASS-with-suffix rather than FAIL). Real
#     drift cannot hide.
#   Prove alternative is strictly better: leaving in place forces
#     the operator to mentally subtract 5 known FAILs from every
#     run - exactly the false-positive load the harness was built
#     to remove.
#
# Verification after this commit lands:
#   1. Re-run section-01-static-config.command - d10's unlanded
#      role FAIL gone; PASS evidence shows "1 planned domain(s)
#      with deferred wiring: d10_research_lab: [...]"
#   2. Re-run section-09-handoff-routing.command - d10's role
#      FAIL gone; PASS evidence shows the deferred suffix.
#   3. diagnostic-all.command - section 01 + section 09 each
#      drop one FAIL.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/diagnostic/section-01-static-config.command \
        dev-tools/diagnostic/section-09-handoff-routing.command \
        dev-tools/commit-bursts/commit-burst365-planned-domain-status-aware-checks.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(harness): planned-domain status-aware entry_agent checks (B365)

Burst 365. Close open bug #4 from the harness baseline punch list.

d10_research_lab.yaml has status: planned and references 5 roles
that haven't been wired into trait_engine yet (gatherer, analyst,
critic, synthesizer, debate_moderator). Per ADR-0067 the role
wiring lands when D10's rollout arc begins (8 arcs from now per
the D4 -> D3 -> D8 -> D1 -> D2 -> D7 -> D9 -> D10 dependency
order). Pre-B365 the harness reported these as FAIL on every
daily run.

Two-file fix:

section-01-static-config.command:
  The 'entry_agents reference real roles' check now skips
  status: planned domains. New sibling check catalogues planned
  domains + their unlanded roles as PASS-with-evidence - one
  read of 'what wiring is queued for which arc.'

section-09-handoff-routing.command:
  Check 2 gains the same planned-skip + deferred-suffix shape.
  Unlanded roles from planned domains append to the PASS
  evidence string.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: daily summary polluted by 5 known-fine FAILs.
  Prove non-load-bearing: strict check still fires for
    non-planned domains; planned domains' unlanded roles still
    surface (PASS-with-suffix).
  Prove alternative is better: leaving in place forces operator
    to mentally subtract 5 false positives every run - exactly
    the load the baseline-aware harness exists to remove.

After this lands: section 01 + section 09 each drop one FAIL."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 365 complete - planned-domain awareness ==="
echo "=========================================================="
echo "Re-test:"
echo "  dev-tools/diagnostic/section-01-static-config.command"
echo "  dev-tools/diagnostic/section-09-handoff-routing.command"
echo "Expected: d10 entry_agent FAILs gone; PASS evidence shows"
echo "  the deferred-wiring suffix."
echo ""
echo "Press any key to close."
read -n 1 || true
