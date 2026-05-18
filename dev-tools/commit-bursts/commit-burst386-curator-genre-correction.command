#!/bin/bash
# Burst 386 - threat_intel_curator genre correction.
#
# Live verify of B385 surfaced a genre kit-tier violation at
# birth time:
#   genre kit-tier violation: 'threat_intel_curator' is in genre
#   'guardian' (max_side_effects=read_only), resolved kit contains
#   tools that exceed that ceiling: web_fetch (network).
#
# Same B341 pattern that hit migration_pilot + release_gatekeeper
# in ADR-0077: a role placed in guardian for the "advisory" framing
# whose kit actually needs network/external/filesystem reach.
# CLAUDE.md memory captured the resolution: genre is action surface;
# advisory stance is enforced via constitutional policies. Move the
# role to the genre whose ceiling its kit actually needs.
#
# For threat_intel_curator, researcher is the right home:
#   - max_side_effects: network (covers web_fetch)
#   - Description matches: "literature scan, data synthesis,
#     knowledge consolidation. Reads broadly with allowlisted
#     network reach (catalog browse, web fetch against allowlists),
#     emits structured summaries."
# That description IS threat-intel curation. Curator is researcher's
# security-domain sibling.
#
# The advisory stance stays intact because the constitution policies
# (forbid_runtime_event_analysis, forbid_response_action,
# forbid_silent_feed_substitution, require_provenance_attestation)
# do NOT depend on genre. They fire at every dispatch regardless of
# the kit-tier ceiling. Genre relaxes WHICH tools the kit can carry;
# policies constrain WHAT the agent does with them.
#
# Files in this commit:
#
#   config/genres.yaml (MOD)
#     - Remove threat_intel_curator from guardian.
#     - Add to researcher.
#     - Inline NOTE captures the B386 reason + the B341 pattern.
#
#   config/trait_tree.yaml (MOD)
#     - Update threat_intel_curator description to say "Genre:
#       researcher" + cite B386 + the reason.
#
#   tests/unit/test_b385_threat_intel_curator_wiring.py (MOD)
#     - Renamed test_threat_intel_curator_is_in_guardian_genre ->
#       test_threat_intel_curator_is_in_researcher_genre.
#     - Asserts researcher contains the role AND guardian no
#       longer does (regression test for the B386 move).
#     - 8 tests pass.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: B385's role cannot be birthed today; birth fails
#     with kit-tier violation. The whole T6 close arc is blocked
#     at the live-verify step.
#   Prove non-load-bearing:
#     - Genre move is the genre-engine-level fix. Tool catalog
#       unchanged. Constitution template unchanged. Policies
#       unchanged.
#     - Researcher genre's other roles (system_architect,
#       test_author, paper_summarizer, etc.) are unaffected.
#   Prove alternative is strictly better:
#     - Drop web_fetch from the kit: defeats the role's purpose
#       (it has nothing to curate).
#     - Move to actuator: actuator allows external reach; harder
#       to constrain than researcher's network ceiling.
#     - Researcher is the smallest ceiling that allows the kit;
#       least-privilege at the genre layer.
#
# Verification after this commit lands:
#   1. PYTHONPATH=src python3 -m pytest tests/unit/test_b385_threat_intel_curator_wiring.py
#      Expected: 8 passed.
#   2. dev-tools/force-restart-daemon.command (loads genre change).
#   3. dev-tools/birth-threat-intel-curator.command — now succeeds.
#   4. dev-tools/diagnostic/diagnostic-all.command — still 14/14
#      PASS; ThreatIntelCurator-D3 appears in section-05.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add config/genres.yaml \
        config/trait_tree.yaml \
        tests/unit/test_b385_threat_intel_curator_wiring.py \
        dev-tools/commit-bursts/commit-burst386-curator-genre-correction.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(genres): threat_intel_curator -> researcher (B386)

Burst 386. Live verify of B385 surfaced a genre kit-tier
violation: threat_intel_curator in guardian (read_only) but kit
needs web_fetch (network). Same B341 pattern that moved
migration_pilot + release_gatekeeper to actuator.

CLAUDE.md memory captured the resolution: genre is action surface;
advisory stance is enforced via constitutional policies. Move
the role to a genre whose ceiling matches the kit.

researcher is the right home for the curator:
  - max_side_effects: network (covers web_fetch)
  - description matches threat-intel curation almost verbatim
    ('literature scan, data synthesis, knowledge consolidation.
    Reads broadly with allowlisted network reach...')
  - smallest ceiling that allows the kit (least-privilege).

The advisory stance stays intact via the four constitution
policies (forbid_runtime_event_analysis, forbid_response_action,
forbid_silent_feed_substitution, require_provenance_attestation).
They fire at every dispatch regardless of the kit-tier ceiling.

Files:
  config/genres.yaml - remove from guardian; add to researcher.
  config/trait_tree.yaml - description says 'Genre: researcher'
    + B386 attribution + reason.
  tests/unit/test_b385_threat_intel_curator_wiring.py - test
    renamed + asserts researcher membership + guardian absence
    (regression test for this move). 8 pass.

After this lands + restart: birth-threat-intel-curator.command
succeeds. ADR-0064 + D3 Phase B remain closed; this is a
post-T6 birth-time correction, not a tranche change."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 386 complete - curator genre corrected ==="
echo "=========================================================="
echo "Re-test:"
echo "  dev-tools/force-restart-daemon.command"
echo "  dev-tools/birth-threat-intel-curator.command  # should succeed"
echo ""
echo "Press any key to close."
read -n 1 || true
