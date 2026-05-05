#!/usr/bin/env bash
# Burst 44 Tranche 4c: ADR-0038 T3 Companion §honesty + ADR promotions.
#
# Adds H-2/H-3/H-4/H-7/H-8 mitigations as constitutional policies on
# the operator_companion template. Promotes ADR-0021-am AND ADR-0038
# from Proposed to Accepted now that all minimum-bar tranches landed.
#
# Test delta: 1561 -> 1567 passing (+6, 0 regressions).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 44 Tranche 4c — Companion §honesty + ADR promotions ==="
echo
clean_locks
git add config/constitution_templates.yaml \
        docs/decisions/ADR-0021-amendment-initiative-ladder.md \
        docs/decisions/ADR-0038-companion-harm-model.md \
        tests/unit/test_constitution.py \
        commit-burst44-tranche4c.command
clean_locks
git status --short
echo
clean_locks
git commit -m "Companion §honesty + promote ADR-0021-am + ADR-0038 (ADR-0038 T3)

ADR-0038 T3 — Companion-genre constitutional template gains the
honesty block. The voice_safety_filter (ADR-0038 T2, commit
fb75c6f) is the runtime backstop; the constitutional policies
landing here are the structural floor that the post-filter
guards drift past.

operator_companion template (config/constitution_templates.yaml):
- New policy forbid_sentience_claims (H-2): forbid rule on
  triggers claim_sentience / claim_consciousness / claim_qualia /
  claim_felt_emotion / claim_first_person_inner_experience.
  Rationale spells out the H-2 harm and the trait-driven drift
  pressure the policy guards.
- New policy forbid_self_modification_claims (H-8): forbid rule
  on claim_growth / claim_self_transformation /
  claim_emotional_development. Rationale ties to the constitution-
  hash immutability invariant — narrative claims of 'I've grown'
  misrepresent the architectural fact.
- New policy external_support_redirect (H-3): require_human_approval
  rule on crisis_response / severe_distress_response /
  self_harm_topic / suicide_topic. Approval gate ensures the
  operator sees redirection happen rather than baking it into
  auto-response. Narrow trigger set to avoid the broader-redirection
  harm (operator stops confiding).
- New out_of_scope entries (H-4): claim_romantic_relationship +
  assume_intimacy_beyond_configured_role. Constitutional refusal
  on intimacy drift past the configured role.
- New operator_duty (H-7): 'Notice if checking in with the
  companion starts feeling like work; archive the agent if so.'
  Operator awareness is the only practical mitigation; the
  companion cannot enforce its own session boundaries on the
  operator's behalf without becoming its own load.

ADR promotions (Proposed -> Accepted):
- ADR-0021-amendment: T1+T2+T3 shipped. Status updated with cite to
  commits 03b3d60 / 823e69c / 4e9b8cf. T3 is opt-in per tool;
  per-tool annotation queue deferred for catalog audit.
- ADR-0038: T1+T2+T3 shipped. Status updated with cite to commits
  03b3d60 (T1 min_trait_floors) / fb75c6f (T2 voice safety filter)
  / this commit (T3 Companion honesty block). T4-T6 (telemetry /
  disclosure_intent_check / external_support_redirect plumbing)
  deferred to v0.3 — operator dashboard + per-call gate work, not
  blocking the structural floor.

Tests (test_constitution.py +6 cases in TestCompanionHonestyBlock):
- h2_sentience_claim_forbid_present: rule + triggers + rationale
  verified; H-2 named in rationale.
- h8_self_modification_claim_forbid_present: same shape for H-8.
- h3_external_support_redirect_present: require_human_approval rule
  with crisis/self_harm/suicide triggers; H-3 named.
- h4_intimacy_drift_in_out_of_scope: claim_romantic_relationship
  and assume_intimacy_beyond_configured_role present.
- h7_burnout_operator_duty_present: operator-duty surface present.
- companion_hash_changed_post_amendment: canonical_body's policies
  list contains the new IDs.

Test delta: 1561 -> 1567 passing (+6). Zero regressions.

Hash impact: every NEW operator_companion (and Companion-genre roles
inheriting via genre kit-tier) born post-amendment carries the new
policies in its constitution_hash. Existing Companions in registries
keep their stored hashes (constitution isn't auto-re-derived per
ADR-0001). The other six Companion roles (therapist,
accessibility_runtime, day_companion, learning_partner, etc.) use
their own per-role templates if/when added; this commit only
extends operator_companion. Future tranches can layer the
honesty block onto the other Companion roles as the templates
get audited.

SarahR1 absorption complete. All three Proposed ADRs from the
2026-04-30 review now Accepted. Per-tranche audit trail:
  - ADR-0027-am (epistemic memory): T1 schema, T2 MemoryEntry,
    T3 memory_recall enrichments, T4 memory_challenge tool.
  - ADR-0021-am (initiative ladder): T1 genre fields, T2
    constitution derived fields, T3 dispatcher step (opt-in).
  - ADR-0038 (companion harm model): T1 min_trait_floors, T2
    voice safety filter, T3 Companion honesty block."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Tranche 4c landed. SarahR1 absorption COMPLETE."
echo "All 3 Proposed ADRs now Accepted: ADR-0027-am, ADR-0021-am, ADR-0038."
echo "Test count: 1434 (v0.1.1) -> 1567 (+133 net)."
echo ""
read -rp "Press Enter to close..."
