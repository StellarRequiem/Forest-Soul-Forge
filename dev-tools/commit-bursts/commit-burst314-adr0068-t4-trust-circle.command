#!/bin/bash
# Burst 314 - ADR-0068 T4: trust circle extension.
#
# Adds operator-declared trust circle to the profile schema.
# Each person carries name + relationship (required) + optional
# email + notes. Reality Anchor seeds expand to include one fact
# per person at HIGH severity so an agent claiming the wrong
# relationship or name gets caught at dispatch time.
#
# What ships:
#
# 1. src/forest_soul_forge/core/operator_profile.py:
#    - TrustCirclePerson frozen dataclass (name, relationship,
#      email Optional, notes Optional).
#    - OperatorProfile gains `trust_circle: tuple[...] = ()`.
#      Backward-compatible: pre-T4 yamls have no trust_circle
#      and default to empty tuple, no schema_version bump needed.
#    - _validate_and_construct now reads operator.trust_circle
#      via the new _parse_trust_circle helper which validates
#      every entry: must be dict, must have name + relationship
#      (non-empty strings), email + notes optional, malformed
#      entries raise OperatorProfileError with index + field.
#    - _to_yaml serializes trust_circle when non-empty; per-person
#      optional fields omitted when None (minimum disclosure +
#      diff stability).
#    - save_operator_profile preserves trust_circle through the
#      updated_at refresh path (the T1 build-OperatorProfile-
#      from-scratch helper missed any future field; explicit
#      forward of trust_circle here sets the pattern for T5+T6).
#    - profile_to_ground_truth_seeds emits one HIGH-severity seed
#      per person. canonical_terms include name + relationship
#      so both "your spouse is X" (wrong name) and "Mira is your
#      coworker" (wrong relationship) surface as contradictions.
#      Email surfaces in the statement when present.
#
# 2. tests/unit/test_operator_profile_trust_circle.py - 14 cases:
#    Dataclass surface:
#      - TrustCirclePerson required-only construction
#      - Optional fields set
#      - OperatorProfile default trust_circle = ()
#    Round-trip + YAML shape:
#      - 3-person round-trip preserves all fields
#      - YAML omits trust_circle when empty
#      - YAML omits per-person optional fields when None
#    Loader refusals:
#      - non-list trust_circle
#      - entry missing name
#      - entry missing relationship
#      - non-string required field
#      - non-dict entry
#    Reality Anchor seeds:
#      - one seed per person
#      - HIGH severity
#      - email in statement when present
#      - email omitted when not present
#      - canonical_terms include name + relationship
#
# Sandbox-verified end-to-end: round-trip, refusals, seeds.
#
# ADR-0068 progress: 5/8 (T1 substrate + T1.1 + T2 write tool +
# T3 personal scope + T4 trust circle). T5-T8 queued: voice
# samples, financial fields, consent wizard, migration substrate.
#
# What's NOT in T4 (queued):
#   - operator_profile_write.v1 field paths for trust_circle
#     entries. Adding list-element mutations (add / remove / edit
#     by name) is a follow-on burst since the current write tool
#     uses dotted field_paths that don't compose with list ops.
#     For now operators edit profile.yaml directly + restart for
#     trust_circle changes.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/operator_profile.py \
        tests/unit/test_operator_profile_trust_circle.py \
        dev-tools/commit-bursts/commit-burst314-adr0068-t4-trust-circle.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(operator): ADR-0068 T4 - trust circle (B314)

Burst 314. Adds operator-declared trust circle to the profile
schema. Each person carries name + relationship (required) +
optional email + notes. Reality Anchor seeds expand to one fact
per person at HIGH severity so an agent claiming the wrong
relationship or name gets caught at dispatch time.

What ships:

  - core/operator_profile.py: TrustCirclePerson frozen dataclass.
    OperatorProfile gains trust_circle: tuple[...] = ()
    (backward-compat with pre-T4 yamls — no schema_version bump
    needed). _parse_trust_circle helper validates every entry
    (dict shape, required name + relationship, non-empty strings),
    raises OperatorProfileError on malformed entries with index +
    field. _to_yaml emits trust_circle when non-empty, omits
    per-person optional fields when None. save_operator_profile
    forwards trust_circle through the updated_at refresh (the T1
    rebuild helper had to be extended; sets the pattern for T5+T6
    to follow). profile_to_ground_truth_seeds emits one HIGH-
    severity seed per person with name + relationship in
    canonical_terms (so both wrong-name and wrong-relationship
    surface as contradictions) and email in the statement when
    present.

Tests: test_operator_profile_trust_circle.py - 14 cases covering
dataclass surface (required-only + optional fields + default
empty tuple), round-trip preservation, YAML omits-when-empty +
omits-optional-fields-when-None, 5 loader refusal paths
(non-list, missing name, missing relationship, non-string
required, non-dict entry), and 5 seed-generation tests
(per-person emission, HIGH severity, email-in-statement
present/absent, canonical_terms shape).

Sandbox-verified end-to-end round-trip + refusals + seeds.

ADR-0068 progress: 5/8 (T1 substrate + T1.1 + T2 write tool +
T3 personal scope + T4 trust circle). T5-T8 queued: voice
samples, financial fields, consent wizard, migration substrate.

operator_profile_write.v1 field_paths for trust_circle entries
(add/remove/edit list elements) is a follow-on burst — current
write tool uses dotted paths that dont compose with list ops.
Operators edit profile.yaml directly + restart for trust_circle
changes in the meantime."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 314 complete - ADR-0068 T4 trust circle shipped ==="
echo ""
echo "Press any key to close."
read -n 1
