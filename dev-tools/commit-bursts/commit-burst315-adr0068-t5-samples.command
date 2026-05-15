#!/bin/bash
# Burst 315 - ADR-0068 T5: voice + writing samples.
#
# Operator's reference materials nested on the profile. Same
# forward-compat shape as T4 trust_circle: optional fields,
# absent defaults to empty tuple, YAML omits when empty. No
# Reality Anchor seeds — these are operational pointers (paths
# to files), not assertion-grade facts.
#
# What ships:
#
# 1. src/forest_soul_forge/core/operator_profile.py:
#    - VoiceSample frozen dataclass: phrase + audio_path
#      (required) + notes (optional). Feeds ADR-0070 TTS
#      personalization at synthesize time.
#    - WritingSample frozen dataclass: title + file_path
#      (required) + channel + notes (both optional). Feeds
#      Content Studio style matching.
#    - OperatorProfile gains voice_samples + writing_samples,
#      both default () for backward-compat with pre-T5 yamls.
#    - _parse_voice_samples + _parse_writing_samples helpers
#      with index + field-named error messages.
#    - _voice_sample_to_dict + _writing_sample_to_dict
#      serializers (omit optional fields when None for
#      diff-stable YAML).
#    - save_operator_profile forwards both new fields through
#      the updated_at refresh (same pattern as T4 trust_circle).
#    - _to_yaml emits each list only when non-empty so
#      operators who haven't recorded any samples get a
#      pre-T5-shaped YAML.
#
# 2. tests/unit/test_operator_profile_samples.py - 15 cases:
#    Dataclass surface (5):
#      - VoiceSample required-only + optional notes
#      - WritingSample required-only + optional channel + notes
#      - Profile defaults both lists empty
#    Round-trip + YAML shape (4):
#      - Voice samples round-trip
#      - Writing samples round-trip
#      - Empty lists omitted from YAML
#      - Per-entry optional fields omitted when None
#    Loader refusals (6):
#      - Parametrized non-list for both lists
#      - Parametrized non-dict entry for both lists
#      - voice_sample missing phrase / audio_path
#      - writing_sample missing title / file_path
#    Reality Anchor (1):
#      - No seeds emitted for voice/writing samples
#
# Sandbox-verified all 6 functional scenarios.
#
# ADR-0068 progress: 6/8 (T1-T5). T6-T8 queued: financial
# fields, consent wizard, migration substrate.
#
# What's NOT in T5 (queued):
#   - operator_profile_write.v1 field paths for sample list
#     mutations. Same list-element editing limitation as T4:
#     current write tool is dotted-flat-paths; list ops queued.
#     Operators edit profile.yaml directly for samples now.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/operator_profile.py \
        tests/unit/test_operator_profile_samples.py \
        dev-tools/commit-bursts/commit-burst315-adr0068-t5-samples.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(operator): ADR-0068 T5 - voice + writing samples (B315)

Burst 315. Two new operator-reference lists on the profile:
voice_samples feeds ADR-0070 TTS pronunciation personalization,
writing_samples feeds Content Studio style matching. Same
forward-compat shape as T4 trust_circle — optional, absent
defaults to empty tuple, YAML omits when empty.

No Reality Anchor seeds: these are operational pointers (paths
to files), not assertion-grade facts. The Reality Anchor would
have nothing useful to pattern-match against.

What ships:

  - core/operator_profile.py: VoiceSample (phrase + audio_path +
    optional notes) and WritingSample (title + file_path +
    optional channel + notes) frozen dataclasses. OperatorProfile
    gains both lists with () defaults. _parse_voice_samples +
    _parse_writing_samples helpers with index + field-named
    error messages on malformed entries. Per-entry serializers
    omit optional fields when None for diff-stable YAML.
    save_operator_profile forwards both fields through the
    updated_at refresh (same pattern as T4). _to_yaml emits
    each list only when non-empty.

Tests: test_operator_profile_samples.py - 15 cases covering
dataclass surface (5), round-trip preservation + YAML
omit-when-empty + per-entry-optional-omit (4), loader refusals
parametrized across both list types (6), and explicit
no-RA-seeds assertion.

Sandbox-verified all 6 functional scenarios.

ADR-0068 progress: 6/8 (T1-T5). T6-T8 queued: financial
fields, consent wizard, migration substrate.

Write-tool field_paths for sample list mutations queued as
follow-on; operators edit profile.yaml directly for sample
list changes in the meantime."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 315 complete - ADR-0068 T5 samples shipped ==="
echo ""
echo "Press any key to close."
read -n 1
