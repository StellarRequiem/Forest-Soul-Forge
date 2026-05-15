#!/bin/bash
# Burst 312 - ADR-0068 T2: operator_profile_write.v1.
#
# Mutating sibling to the B277 read tool. Takes a dotted
# field_path + new_value + reason, atomically updates the
# operator profile on disk, emits operator_profile_changed
# audit event with before/after diff. side_effects=filesystem
# + requires_human_approval=True so operator-truth mutations
# always gate per-call.
#
# What ships:
#
# 1. src/forest_soul_forge/core/audit_chain.py:
#    KNOWN_EVENT_TYPES gains "operator_profile_changed" so the
#    audit-chain verifier accepts the new event family.
#
# 2. src/forest_soul_forge/tools/builtin/operator_profile_write.py:
#    OperatorProfileWriteTool with validate + call. Supported
#    field_paths (T2): name, preferred_name, email, timezone,
#    locale, work_hours.start, work_hours.end. Future tranches
#    extend under OperatorProfile.extra for trust_circle /
#    voice_samples / financial_jurisdiction.
#
#    Behavior:
#      - validate raises ToolValidationError on missing args,
#        non-string types, empty field_path/reason, unsupported
#        field_path, malformed HH:MM for work_hours.
#      - call loads profile (encryption-aware via ctx.master_key),
#        computes new value via dataclasses.replace, atomically
#        saves via save_operator_profile, emits
#        operator_profile_changed with (field_path, before,
#        after, reason, operator_id, schema_version), re-runs
#        profile_to_ground_truth_seeds + surfaces in metadata
#        for the operator's manual Reality Anchor reload.
#      - No-op semantics: if new_value == before, skip write
#        and audit emit; output.no_op=True.
#      - Best-effort audit emit: a chain failure after the disk
#        write doesn't roll back (rolling back leaves operator
#        profile in mid-write state, worse than a chain gap).
#
# 3. src/forest_soul_forge/tools/builtin/__init__.py:
#    Imports OperatorProfileWriteTool, adds to __all__,
#    registers in the builtin registry.
#
# 4. config/tool_catalog.yaml:
#    operator_profile_write.v1 entry with input_schema enumerating
#    the seven supported field_paths, side_effects=filesystem,
#    requires_human_approval=true. archetype_tags: assistant +
#    operator_steward (the tool is for the operator's own
#    assistant, not domain agents).
#
# Tests (test_operator_profile_write.py - 13 cases):
#   Validation:
#     - missing field_path / new_value / reason refused
#     - unsupported field_path refused
#     - bad work_hours format refused
#     - all 7 supported paths accepted
#   Happy path:
#     - top-level field update
#     - nested work_hours.start updates child but preserves end
#     - operator_profile_changed audit event emitted with
#       full payload shape
#     - reality_anchor_seeds in metadata after write
#   No-op:
#     - unchanged value -> no_op=True, no audit emit
#   Surface invariants:
#     - requires_human_approval is True
#     - side_effects is filesystem
#     - sandbox_eligible is False
#
# Sandbox-verified all 8 functional scenarios.
#
# What's NOT in T2 (queued):
#   T3: `personal` memory scope — fifth scope value (alongside
#       private/lineage/realm/consented). Validator allow-list +
#       per-genre default read permissions.
#   T4-T6: profile extensions (trust_circle, voice_samples,
#       financial_jurisdiction) under OperatorProfile.extra.
#   T7: cross-domain consent prompts (first-boot wizard).
#   T8: profile migration substrate (schema-version-aware loader,
#       v1->v2 helpers).

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/audit_chain.py \
        src/forest_soul_forge/tools/builtin/operator_profile_write.py \
        src/forest_soul_forge/tools/builtin/__init__.py \
        config/tool_catalog.yaml \
        tests/unit/test_operator_profile_write.py \
        dev-tools/commit-bursts/commit-burst312-adr0068-t2-profile-write.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(operator): ADR-0068 T2 - operator_profile_write.v1 (B312)

Burst 312. Mutating sibling to the B277 read tool. Field-path
based partial update on data/operator/profile.yaml with full
governance + audit shape.

What ships:

  - core/audit_chain.py: KNOWN_EVENT_TYPES gains
    operator_profile_changed so the verifier accepts T2 emits.

  - tools/builtin/operator_profile_write.py: OperatorProfileWriteTool
    with validate + call. Seven supported field_paths at T2:
    name, preferred_name, email, timezone, locale,
    work_hours.start, work_hours.end. validate refuses missing
    args / unsupported paths / malformed HH:MM. call loads
    profile (encryption-aware), computes new value via
    dataclasses.replace (nested WorkHours preserves its sibling
    field), atomically saves, emits operator_profile_changed
    with full diff payload + reason + operator_id, re-runs
    profile_to_ground_truth_seeds and surfaces in metadata for
    the operators manual Reality Anchor reload. No-op short-
    circuit when new_value == before (no write, no audit).
    side_effects=filesystem + requires_human_approval=True so
    operator-truth always gates per-call; sandbox_eligible=False
    (writes to data/).

  - tools/builtin/__init__.py: import + __all__ + register the
    new tool in the builtin registry.

  - config/tool_catalog.yaml: operator_profile_write.v1 entry
    with input_schema enumerating the seven field_paths,
    side_effects=filesystem, requires_human_approval=true,
    archetype_tags: assistant + operator_steward.

Tests: test_operator_profile_write.py - 13 cases covering
validation refusals (3 missing-arg paths + unsupported field_path
+ bad work_hours format + all 7 valid paths), happy-path top-level
+ nested writes, audit event payload shape, RA seeds metadata,
no-op skip + no-audit-emit, and three surface invariants
(requires_human_approval / side_effects / sandbox_eligible).

Sandbox-verified all 8 functional scenarios.

ADR-0068 progress: 3/8 (T1 substrate + T1.1 ground-truth merge +
T2 write tool). T3-T8 queued: personal memory scope, trust
circle extension, voice samples, financial fields, consent
wizard, migration substrate."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 312 complete - ADR-0068 T2 shipped ==="
echo ""
echo "Press any key to close."
read -n 1
