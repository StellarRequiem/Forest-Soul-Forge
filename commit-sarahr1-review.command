#!/usr/bin/env bash
# Commit + push the SarahR1 (Irisviel) review absorption.
#
# Five files land in one coherent commit per CLAUDE.md convention:
#   - docs/decisions/ADR-0038-companion-harm-model.md          (Proposed)
#   - docs/decisions/ADR-0027-amendment-epistemic-metadata.md  (Proposed)
#   - docs/decisions/ADR-0021-amendment-initiative-ladder.md   (Proposed)
#   - CREDITS.md                                               (new)
#   - docs/audits/2026-05-01-sarahr1-review-response.md        (new)
#
# All three ADRs stay Proposed until orchestrator promotion.
#
# Handles recurring sandbox lock cleanup before each git op.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

BLUE="\033[1;34m"
GREEN="\033[1;32m"
YELLOW="\033[1;33m"
RED="\033[1;31m"
RESET="\033[0m"

say()  { printf "${BLUE}[sarahr1]${RESET} %s\n" "$*"; }
ok()   { printf "${GREEN}[sarahr1]${RESET} %s\n" "$*"; }
warn() { printf "${YELLOW}[sarahr1]${RESET} %s\n" "$*"; }
err()  { printf "${RED}[sarahr1]${RESET} %s\n" "$*" 1>&2; }

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

say "=== SarahR1 review absorption commit + push ==="
echo

clean_locks
ok "step 0/4 — locks cleared"

say "step 1/4 — staging review-absorption files..."
git add CREDITS.md \
        docs/audits/2026-05-01-sarahr1-review-response.md \
        docs/decisions/ADR-0021-amendment-initiative-ladder.md \
        docs/decisions/ADR-0027-amendment-epistemic-metadata.md \
        docs/decisions/ADR-0038-companion-harm-model.md
clean_locks
STAGED_COUNT=$(git status --short | grep -c "^[AM]")
ok "  staged $STAGED_COUNT files"
git status --short
echo

say "step 2/4 — commit..."
clean_locks
git commit -m "Absorb SarahR1 (Irisviel) review — 3 Proposed ADRs + CREDITS

External reviewer SarahR1 (Nexus / Irkalla project) provided a
comparative review of FSF on 2026-04-30. Three substantive gaps in
the review were absorbed as Proposed ADRs; declined recommendations
are documented in CREDITS.md with reasoning.

Files added:

- docs/decisions/ADR-0038-companion-harm-model.md (291 lines)
  New ADR. Eight-harm Companion-tier taxonomy: H-1 sycophancy,
  H-2 false sentience claims, H-3 emotional dependency loop,
  H-4 intimacy drift / role escalation, H-5 privacy leakage
  through helpfulness, H-6 memory overreach / inferred-preference
  cementing, H-7 operator burnout, H-8 self-improvement narrative
  inflation. Per-harm mitigation surface mapping. New
  min_trait_floors mechanic on genres.yaml symmetric to the
  existing max_side_effects ceiling.

- docs/decisions/ADR-0027-amendment-epistemic-metadata.md (409 lines)
  Amends ADR-0027. Adds claim_type (six-class enum: observation /
  user_statement / agent_inference / preference / promise /
  external_fact), three-state confidence, separate
  memory_contradictions table, last_challenged_at field. Schema
  bump v10 -> v11, additive only. K1 memory_verify.v1 stays in
  force; verification combines with confidence at read time.
  Closes ADR-0038 H-6 at the data layer.

- docs/decisions/ADR-0021-amendment-initiative-ladder.md (408 lines)
  Amends ADR-0021. Adds max_initiative_level (L0-L5 ladder)
  orthogonal to existing max_side_effects. Per-genre defaults +
  ceilings: Companion L2 max / L1 default; Engineer L4 (reversible
  side-effects with policy); Actuator L5 (destructive with
  friction). New InitiativeFloorStep in R3 governance pipeline.
  Constitution-hash bumps for re-derived agents.

- CREDITS.md (73 lines)
  New attribution-discipline file. SarahR1 is the first entry;
  documents both adopted contributions and declined-with-reasoning
  entries (embodied state, soul-architecture misread, stale
  dict/list bug claim, stale integration-test count).

- docs/audits/2026-05-01-sarahr1-review-response.md (164 lines)
  Saved response in audit trail. Disk-citation corrections of
  stale claims, three adoption announcements, three pushbacks,
  three questions back at her.

All three new ADRs sit at Proposed status until orchestrator
promotion. Promotion path is the same as the eight Phase D ADRs
that landed in v0.1.1 (status update + audit doc citation).

No code changed. No schema changed yet. No runtime behavior
changed. This commit is decision-record + attribution only;
implementation tranches are queued in each ADR's tranche list
and gate on orchestrator sign-off.

External catalyst: github.com/SarahR1 (display name Irisviel),
public surface github.com/SarahR1/nexus-portfolio."

clean_locks
ok "  commit landed"
echo

say "step 3/4 — push..."
clean_locks
git push origin main
clean_locks
ok "  pushed to origin"
echo

say "step 4/4 — final state"
git log -1 --oneline
echo
ok "SarahR1 review absorption landed on origin."
echo
echo "Three ADRs Proposed. Awaiting orchestrator promotion."
echo "Response draft at docs/audits/2026-05-01-sarahr1-review-response.md"
echo ""
read -rp "Press Enter to close..."
