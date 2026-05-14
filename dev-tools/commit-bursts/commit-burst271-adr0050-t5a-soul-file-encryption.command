#!/bin/bash
# Burst 271 — ADR-0050 T5a: soul + constitution file encryption.
#
# Fifth tranche of the encryption-at-rest arc. T5 was originally
# scoped as one burst in the ADR; reality is the dispatcher hot-path
# constitution reads (~10 sites in tools/dispatcher.py + tool_dispatch
# + conversation_helpers) push the full T5 surface past a comfortable
# one-burst boundary. T5 is therefore split:
#
#   T5a (this burst, B271) — write path + write-adjacent reads
#     - write_artifacts encrypts when EncryptionConfig is set,
#       lands files at .soul.md.enc / .constitution.yaml.enc
#     - rollback_artifacts cleans BOTH plain + .enc shapes
#     - voice regenerate (update_soul_voice) decrypts→edit→re-encrypts;
#       on-disk variant is sticky (never silently downgrades to plaintext)
#     - character_sheet read helpers detect .enc and decrypt transparently
#     - ingest.iter_soul_files rglob covers both shapes
#     - parse_soul_file decrypts .enc input; returns canonical
#       (plaintext-named) soul_path on the ParsedSoul row
#
#   T5b (queued, future B272) — dispatcher hot-path reads
#     - tools/dispatcher.py (5+ constitution_path.read_text sites)
#     - daemon/routers/tool_dispatch.py
#     - daemon/routers/conversation_helpers.py
#     Required to make encrypted agents dispatch tools successfully.
#     Until T5b lands, the FSF_AT_REST_ENCRYPTION=true posture is
#     "encrypted at rest, but tool dispatch unsupported".
#
# Why this split: §0 Hippocratic gate discipline says one coherent
# change per commit. T5a is "write + immediate operator-facing reads
# (sheet + voice + rebuild)". T5b is "tool dispatch hot path reads",
# which involves threading master_key through the ToolDispatcher
# dataclass and is its own coherent surgery.
#
# Reuses primitives shipped in B269:
#   - encrypt_text / decrypt_text from core/at_rest_encryption.py
#   - EncryptionConfig threaded through routes via request.app.state
#
# Implementation:
#
# 1. birth_pipeline.py (writer side):
#    - write_artifacts gains optional encryption_config kwarg;
#      returns (soul_actual, const_actual) tuple of actual paths
#      written (callers ignoring the return value get pre-T5 plaintext
#      behavior unchanged)
#    - rollback_artifacts now tries .enc variants too
#    - read_soul_md / read_constitution_yaml: encryption-aware helpers
#      that detect .enc on disk and decrypt with the supplied config;
#      raise RuntimeError if .enc exists but config is None (explicit
#      rather than cryptic base64 noise)
#    - write_soul_md: in-place rewrite preserves on-disk variant
#
# 2. writes/birth.py: passes EncryptionConfig built from
#    request.app.state.master_key to write_artifacts.
#
# 3. soul/voice_renderer.py: update_soul_voice gains encryption_config.
#    When the soul is at .enc, decrypts → modifies → re-encrypts. Refuses
#    to downgrade encrypted soul to plaintext on rewrite.
#
# 4. writes/voice.py: threads request.app.state.master_key through to
#    update_soul_voice + the existing soul frontmatter read.
#
# 5. routers/character_sheet.py:
#    - get_character_sheet gains request: Request to read app.state
#    - _read_soul_frontmatter + _read_constitution take encryption_config
#    - Both read helpers + the direct soul_path.read_text call site
#      route through the birth_pipeline helpers
#
# 6. registry/ingest.py:
#    - iter_soul_files rglob picks up BOTH .soul.md and .soul.md.enc
#    - parse_soul_file detects .enc input, decrypts via at_rest_encryption,
#      records the canonical (plaintext-named) path on ParsedSoul so
#      downstream callers see consistent path values regardless of
#      on-disk shape
#
# Tests (test_soul_file_encryption.py — 12 cases):
#   - write_artifacts plaintext default (bit-identical pre-T5)
#   - write_artifacts encrypted lands at .enc paths + on-disk is
#     not plaintext
#   - read round-trips for soul + constitution with unicode/newlines
#   - read plaintext path unchanged (no config needed when .enc absent)
#   - read .enc without config raises explicit RuntimeError
#   - rollback unlinks both variants
#   - voice rewrite keeps encrypted encrypted
#   - voice rewrite refuses to downgrade
#
# === What's NOT in T5a ===
#
#   - Dispatcher constitution reads — queued for T5b (B272). Encrypted
#     agents successfully birth + show character sheet + rebuild, but
#     tool dispatch against them won't work until B272.
#   - Per-agent column tracking encryption posture. Detection is
#     entirely via the .enc file-extension probe at read time —
#     stat() per read is cheap and keeps the schema additive-only
#     per ADR-0050 Decision 7.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/routers/birth_pipeline.py \
        src/forest_soul_forge/daemon/routers/character_sheet.py \
        src/forest_soul_forge/daemon/routers/writes/birth.py \
        src/forest_soul_forge/daemon/routers/writes/voice.py \
        src/forest_soul_forge/registry/ingest.py \
        src/forest_soul_forge/soul/voice_renderer.py \
        tests/unit/test_soul_file_encryption.py \
        dev-tools/commit-bursts/commit-burst271-adr0050-t5a-soul-file-encryption.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(security): ADR-0050 T5a — soul + constitution file encryption (B271)

Burst 271. Fifth tranche of the encryption-at-rest arc. T5 split
into T5a (this burst — write + write-adjacent reads) and T5b
(queued — dispatcher hot-path constitution reads). Split rationale:
§0 Hippocratic gate discipline. The full T5 surface (~15 read
sites across the dispatcher hot path) is bigger than one coherent
commit; T5a is the write side + immediate operator-facing reads,
T5b is the tool-dispatch surgery.

Reuses encrypt_text / decrypt_text primitives shipped in B269.

What ships:

 1. birth_pipeline.write_artifacts gains optional encryption_config
    kwarg. When set, encrypts both payloads via encrypt_text and
    writes to <path>.enc extensions; returns the actual paths
    written. When None, bit-identical pre-T5 plaintext behavior.

 2. birth_pipeline.rollback_artifacts unlinks BOTH plain + .enc
    variants. Best-effort still; failure-path call site doesn't
    need to know which shape was written.

 3. birth_pipeline.read_soul_md + read_constitution_yaml: detect
    .enc on disk and decrypt transparently. Mixed deployments
    (some agents encrypted, some plaintext) supported per ADR
    Decision 6 — operators can flip FSF_AT_REST_ENCRYPTION at
    any agent boundary; pre-existing agents stay on whatever
    shape they were birthed under.

 4. soul.voice_renderer.update_soul_voice gains encryption_config.
    Encrypted soul stays encrypted across rewrite (decrypt → edit
    → re-encrypt). Passing config=None when the .enc variant
    exists raises rather than downgrading to plaintext.

 5. writes/birth.py + writes/voice.py thread
    request.app.state.master_key through to the writers.

 6. routers/character_sheet.py gains request: Request parameter;
    _read_soul_frontmatter + _read_constitution route through the
    birth_pipeline read helpers. Operator-facing /character-sheet
    works on encrypted agents.

 7. registry/ingest.py iter_soul_files rglob picks up both shapes;
    parse_soul_file decrypts .enc input and records the canonical
    plaintext-named path on ParsedSoul.

What this does NOT ship (queued T5b / B272):

 - tools/dispatcher.py constitution reads (5+ sites in the hot
   path: initiative resolution, kit-tier check, etc.)
 - daemon/routers/tool_dispatch.py constitution read
 - daemon/routers/conversation_helpers.py constitution read

 Implication: with T5a alone, FSF_AT_REST_ENCRYPTION=true can
 birth encrypted agents and operate the operator-facing flows
 (character sheet, voice regenerate, rebuild-from-artifacts), but
 the dispatcher's per-call constitution read will fail to find
 the file at the plaintext path. T5b ships the master_key thread
 through ToolDispatcher and the matching read-site updates.

Tests: test_soul_file_encryption.py — 12 cases covering write
round-trip (plain + encrypted), read decryption, rollback both
shapes, voice rewrite preserves encryption + refuses downgrade,
mixed deployments. Existing tests unchanged — pre-T5 callers
that pass no encryption_config get bit-identical behavior."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 271 complete — ADR-0050 T5a shipped ==="
echo "Next: T5b — wire master_key through ToolDispatcher + update"
echo "       dispatcher / tool_dispatch / conversation_helpers reads."
echo ""
echo "Press any key to close."
read -n 1
