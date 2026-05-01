#!/usr/bin/env bash
# Burst 47: Draft three v0.3 ADRs as Proposed.
#
# ADR-0035 Persona Forge (layered identity / self-model proposals)
# ADR-0036 Verifier Loop (auto-detected memory contradictions)
# ADR-0037 Observability dashboard (operator-facing telemetry)
#
# All three trace to the SarahR1 review absorption — drafted as
# Proposed status so the next reviewer/contributor sees the v0.3
# queue. Pure-documentation commit; no code, no tests changed.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 47 — three v0.3 ADRs as Proposed ==="
echo
clean_locks
git add docs/decisions/ADR-0035-persona-forge.md \
        docs/decisions/ADR-0036-verifier-loop.md \
        docs/decisions/ADR-0037-observability-dashboard.md \
        CREDITS.md \
        commit-burst47.command
clean_locks
git status --short
echo
clean_locks
git commit -m "Draft ADR-0035 + ADR-0036 + ADR-0037 (Proposed; v0.3 candidates)

Three ADRs filed at Proposed status. All three trace back to the
SarahR1 (Irisviel) review absorption — they were the v0.3 candidates
named in the original review-response thread but not implemented in
v0.1.2. Drafting them now lets the next contributor see the queue
and lets future reviewers see we're tracking it.

ADR-0035 — Persona Forge (docs/decisions/ADR-0035-persona-forge.md)
  Layered identity surface: birth-time constitution stays content-
  addressed and immutable (per ADR-0001); runtime 'persona' is the
  union of constitutional trait_emphasis + ratified proposals
  targeting the constitution_hash. New persona/<dna>/<instance_id>/
  artifact tree with proposals/ + ratified/ subdirs. Five new
  audit-chain event types (persona_proposal_drafted /
  modified / ratified / rejected / superseded). Drift-detection
  inputs: trait alignment scan, preference accretion via
  claim_type='preference', external correction via
  memory_contradictions. H-1 / H-8 floor checks at proposal-draft
  time so persona evolution can't violate Companion's
  min_trait_floors or fabricate self-modification claims. Operator-
  only ratification path. Adopted from SarahR1's 'self as
  maintained pattern' framing; constitution-immutability constraint
  is FSF-specific work that closes the 'soul-as-artifact-needs-
  evolution' misread documented in the response thread.

ADR-0036 — Verifier Loop (docs/decisions/ADR-0036-verifier-loop.md)
  Auto-detected memory contradictions via Verifier as a Guardian-
  genre agent (NOT a daemon-side cron). Per-target schedule + on-
  demand /verifier/scan endpoint. Candidate-pair pre-filter
  (cheap word-overlap heuristic at v0.3) + LLM classification
  (llm_think.v1 with constrained prompt + min_confidence_to_act
  >= 0.8 floor). New memory_flag_contradiction.v1 tool. New
  flagged_state column on memory_contradictions (schema v12)
  with lifecycle: flagged_unreviewed -> flagged_confirmed /
  flagged_rejected. K1 verification + this loop together = full
  Iron Gate semantic (verify is promotion-toward-trust, this is
  demotion-toward-skepticism). Cross-agent scan deferred to v0.4.

ADR-0037 — Observability dashboard (docs/decisions/ADR-0037-observability-dashboard.md)
  Operator-facing telemetry dashboard. Three sub-views: Companion
  safety (ADR-0038 H-3 / H-4 / H-7 — session telemetry, dependency
  signal, boundary report); Memory health (Verifier track record,
  contradiction queue, staleness pressure); Persona drift (ADR-0035
  proposal queue, ratified history, drift indicators). Strictly
  read-only from agent perspective — the dashboard staying purely
  operator-facing is the H-3 manipulation-vector mitigation
  SarahR1's review emphasized. Four new daemon endpoints; one new
  schema migration (companion_session_telemetry table). 5-minute
  per-request cache; no real-time push at v0.3.

CREDITS.md updated:
- Three new rows in the 'Adopted into the codebase' table.
- v0.1.2-shipped ADRs marked Accepted with tranche citations.
- v0.3 candidates marked Proposed.

No code changes. No test changes. Test count unchanged (1577).

These three ADRs do not commit FSF to v0.3 timelines or block
v0.2 close. They sit at Proposed to capture pre-decided design
choices that future implementation work can plan against."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 47 landed. Three v0.3 ADRs filed as Proposed."
echo ""
read -rp "Press Enter to close..."
