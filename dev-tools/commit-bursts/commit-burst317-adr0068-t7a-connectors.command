#!/bin/bash
# Burst 317 - ADR-0068 T7a: connector consent substrate.
#
# Splits T7 into two bursts. T7a (this) ships the data layer +
# audit event + HTTP endpoint. T7b (next burst) ships the first-
# boot wizard frontend pane that drives it.
#
# What ships:
#
# 1. src/forest_soul_forge/core/operator_profile.py:
#    - ConnectorConsent frozen dataclass: domain_id +
#      connector_name + status (granted/denied/pending) +
#      optional decided_at + notes.
#    - _CONNECTOR_STATUS_VALUES frozenset for the enum.
#    - OperatorProfile.connectors: tuple[...] = () (default empty,
#      backward-compat with pre-T7 yamls).
#    - _parse_connectors: validates list shape, per-entry shape,
#      status enum, decided_at-required-when-non-pending,
#      duplicate-pair detection. Raises with index + field on any
#      malformed entry.
#    - _connector_to_dict serializer omits optional fields when
#      None; _to_yaml emits connectors only when non-empty.
#    - save_operator_profile forwards connectors through the
#      updated_at refresh (same pattern as T4-T6).
#    - upsert_connector_consent pure function: replaces existing
#      (domain_id, connector_name) entry or appends new. Auto-
#      stamps decided_at via _now_iso when status != 'pending'
#      and caller didn't supply one. Returns a new OperatorProfile;
#      input is unchanged.
#
# 2. src/forest_soul_forge/core/audit_chain.py:
#    KNOWN_EVENT_TYPES gains 'operator_connector_consent_changed'
#    so the verifier accepts T7 emits.
#
# 3. src/forest_soul_forge/daemon/routers/operator.py (new):
#    Two endpoints under /operator:
#      GET  /operator/profile/connectors
#           List current consent state for every (domain_id,
#           connector_name) pair. Read-only, gated by api_token.
#      POST /operator/connectors/{domain_id}/{connector_name}
#           Body: {status, notes?}. Upserts the consent record,
#           atomically saves the profile, emits
#           operator_connector_consent_changed with before/after
#           status + notes. Gated by writes_enabled + api_token.
#    Encryption-aware: pulls master_key from app.state when
#    at-rest encryption is on.
#
# 4. src/forest_soul_forge/daemon/app.py:
#    Imports + includes the new operator router.
#
# Tests (test_operator_profile_connectors.py - 18 cases):
#   Dataclass + defaults (3)
#   Round-trip + YAML shape (3)
#   Loader refusals (5 + parametrized missing-required = 8)
#   upsert_connector_consent (6):
#     - replaces existing
#     - appends new
#     - auto-stamps decided_at for non-pending
#     - leaves decided_at None for pending
#     - refuses bad status
#     - input profile unchanged (pure function check)
#   Audit event registration (1)
#
# Sandbox-verified all 10 functional scenarios.
#
# What's NOT in T7a (queued for T7b):
#   - Frontend first-boot wizard pane. Walks the operator through
#     each domain's declared connectors, calls POST per choice.
#     HTML + JS module + tab wiring.
#
# ADR-0068 progress: 7.5/8 (T1-T6 + T7a). T7b + T8 queued.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/operator_profile.py \
        src/forest_soul_forge/core/audit_chain.py \
        src/forest_soul_forge/daemon/routers/operator.py \
        src/forest_soul_forge/daemon/app.py \
        tests/unit/test_operator_profile_connectors.py \
        dev-tools/commit-bursts/commit-burst317-adr0068-t7a-connectors.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(operator): ADR-0068 T7a - connector consent substrate (B317)

Burst 317. Splits T7 into two bursts: T7a (this) ships the data
layer + audit event + HTTP endpoint; T7b (next) ships the
first-boot wizard frontend pane.

What ships:

  - core/operator_profile.py: ConnectorConsent frozen dataclass
    (domain_id + connector_name + status + optional decided_at +
    notes). _CONNECTOR_STATUS_VALUES frozenset enums
    granted/denied/pending. OperatorProfile.connectors defaults
    to () for backward-compat. _parse_connectors validates list +
    per-entry shape + status enum + decided_at-required-for-
    non-pending + duplicate-pair detection. _connector_to_dict
    omits optional fields when None. save_operator_profile
    forwards through updated_at refresh. upsert_connector_consent
    pure function returns new profile with the entry replaced or
    appended; auto-stamps decided_at when status != 'pending'.

  - core/audit_chain.py: KNOWN_EVENT_TYPES gains
    operator_connector_consent_changed.

  - daemon/routers/operator.py (new): GET
    /operator/profile/connectors lists current consent state.
    POST /operator/connectors/{domain_id}/{connector_name}
    upserts one consent with status + optional notes, atomically
    saves, emits the audit event with old + new status.
    Encryption-aware (pulls master_key from app.state).

  - daemon/app.py: registers the new operator router.

Tests: test_operator_profile_connectors.py - 18 cases covering
dataclass surface (3), round-trip + YAML omit-when-empty (3),
loader refusals (8 incl parametrized missing-required + bad
status + decided_at-required + duplicate-pair), 6 upsert
behaviors (replace + append + auto-decided_at + None-for-pending
+ bad-status refused + pure-function input-unchanged), audit
event registration.

Sandbox-verified all 10 functional scenarios.

ADR-0068 progress: 7.5/8 (T1-T6 + T7a). T7b frontend wizard +
T8 migration substrate queued."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 317 complete - ADR-0068 T7a connector consent substrate shipped ==="
echo ""
echo "Press any key to close."
read -n 1
