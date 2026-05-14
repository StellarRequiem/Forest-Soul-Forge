#!/bin/bash
# Burst 277 — ADR-0068 T1: operator profile substrate.
#
# Foundation for the ten-domain platform arc. Every domain (D1-D10)
# needs to read "who is the operator?" without re-asking the human
# every conversation. T1 ships the substrate: a versioned YAML file,
# a frozen dataclass loader, a builtin read tool, a CLI surface, and
# Reality Anchor seed generation so personal facts become
# tamper-evident the same way audit-chain entries are.
#
# What ships:
#
# 1. data/operator/profile.yaml — seeded with Alex's basics
#    (operator_id, name, email, timezone=America/New_York placeholder,
#    locale=en-US, work_hours 09:00-17:00). Operator-editable; daemon
#    hot-reloads on POST /operator/profile/reload (queued T2).
#
# 2. core/operator_profile.py — loader + validator + atomic writer +
#    Reality Anchor seed generator. Frozen OperatorProfile dataclass.
#    Encryption-aware: detects .enc variant via ADR-0050 T5 pattern,
#    decrypts on read, encrypts on write when EncryptionConfig is set.
#
# 3. tools/builtin/operator_profile_read.v1 — read_only builtin tool.
#    Any agent with constitution permission can ask "who is the
#    operator?" and get the canonical answer. Audit event captures
#    operator_id + schema_version (not full PII).
#
# 4. cli/operator_cmd.py — fsf operator profile {show, verify, init}.
#    show prints as JSON; verify validates schema + exits non-zero
#    on failure; init bootstraps a fresh profile from CLI flags.
#
# 5. cli/main.py — wires fsf operator subcommand into the root parser.
#
# 6. ADR-0068 — full architectural decision record. Four decisions:
#    YAML file (not registry table), Reality Anchor seeding, fifth
#    memory scope ('personal', queued T3), read tool as primitive.
#
# Tests (test_operator_profile.py — 15 cases):
#   - default path resolution
#   - save+load round-trip with updated_at refresh
#   - atomic write (no .tmp leftover)
#   - encryption-aware save lands at .enc, load decrypts, no-config raises
#   - all failure modes: missing file, malformed YAML, schema mismatch,
#     missing required fields, bad email format, bad timezone,
#     bad work_hours format
#   - ground-truth seed generation: all 6 default seeds present,
#     preferred_name seed omitted when redundant
#
# What's NOT in T1 (queued):
#   - T2: operator_profile_write.v1 + approval-gated CLI 'set'
#   - T3: 'personal' memory scope wired into the validator
#   - T4: trust circle extension
#   - T5: content + voice samples extension
#   - T6: financial + jurisdiction extension
#   - T7: first-boot consent wizard
#   - T8: schema migration substrate
#   - Daemon lifespan integration (read profile + seed Reality Anchor
#     ground-truth at boot) — queued for B278 to keep T1 contained
#   - Tool catalog YAML registration — queued for B278 alongside
#     dispatcher integration; this commit's tool ships the module
#     but isn't dispatch-callable until catalog registration

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0068-personal-context-store.md \
        src/forest_soul_forge/core/operator_profile.py \
        src/forest_soul_forge/tools/builtin/operator_profile_read.py \
        src/forest_soul_forge/cli/operator_cmd.py \
        src/forest_soul_forge/cli/main.py \
        examples/operator/profile.example.yaml \
        tests/unit/test_operator_profile.py \
        dev-tools/commit-bursts/commit-burst277-adr0068-t1-operator-profile.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(operator): ADR-0068 T1 — operator profile substrate (B277)

Burst 277. First tranche of the ten-domain platform arc — the
foundational substrate every domain reads to know who the operator
is. Closes the gap where Forest's kernel had no canonical
operator-identity layer; every agent re-asked the same questions in
conversation.

What ships:

  - data/operator/profile.yaml schema v1: operator_id, name,
    preferred_name, email, timezone, locale, work_hours.
    Operator-editable YAML; matches existing config patterns
    (genres.yaml, ground_truth.yaml, security_iocs.yaml).

  - core/operator_profile.py: frozen OperatorProfile dataclass +
    load + validate + atomic save. Encryption-aware per ADR-0050
    T5 — detects .enc variant on disk, decrypts via encrypt_text
    round-trip when EncryptionConfig is supplied.

  - tools/builtin/operator_profile_read.v1: read_only builtin
    that any agent with constitution permission calls. Audit
    payload captures operator_id + schema_version, not PII.

  - cli/operator_cmd.py: fsf operator profile {show, verify, init}.
    show prints JSON; verify validates + non-zero exit on failure;
    init bootstraps from CLI flags with --force overwrite gate.

  - profile_to_ground_truth_seeds(): translates the profile into
    Reality Anchor (ADR-0063) ground-truth catalog entries.
    Personal facts get the same tamper-evidence as system invariants.
    Seeded entries: operator_name, operator_email, operator_timezone,
    operator_locale, operator_work_hours, optional
    operator_preferred_name (only when distinct from name).

  - ADR-0068 full record. Four decisions: YAML over registry table,
    Reality Anchor seeding, fifth 'personal' memory scope (queued
    T3), operator_profile_read as canonical primitive.

Tests: test_operator_profile.py — 15 cases covering path
resolution, save+load round-trip, atomic write, encryption-aware
save+load, all failure modes (missing/malformed/schema-mismatch/
missing-field/bad-email/bad-timezone/bad-work-hours), ground-truth
seed generation.

Queued tranches T2-T8 cover write tool, personal memory scope,
trust circle, content/voice samples, financial/jurisdiction,
first-boot consent wizard, and schema migration substrate.

Queued B278: daemon lifespan integration (read profile + seed
Reality Anchor catalog at boot) + tool catalog registration so
operator_profile_read is dispatch-callable."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 277 complete — ADR-0068 T1 operator profile shipped ==="
echo "Next: B278 daemon lifespan integration + tool catalog wiring."
echo ""
echo "Press any key to close."
read -n 1
