#!/bin/bash
# Burst 272 — ADR-0050 T5b: dispatcher hot-path constitution reads
# encryption-aware.
#
# Closes the gap left by T5a (B271). T5a shipped the write path +
# write-adjacent reads (birth + voice rewrite + character_sheet +
# ingest). T5b ships the dispatcher hot-path reads so encrypted
# agents successfully dispatch tools.
#
# After this burst lands, an operator who sets FSF_AT_REST_ENCRYPTION
# =true and births a new agent can:
#   - have soul.md + constitution.yaml on disk as .enc variants only
#   - see the agent through the operator-facing character sheet
#   - regenerate the agent's voice (decrypt → modify → re-encrypt)
#   - rebuild the registry from artifacts
#   - dispatch tools through the agent's constitution (this burst's
#     gap closure)
#
# What's modified:
#
# 1. tools/dispatcher.py:
#    - New _read_constitution_text helper at module level: encryption-
#      aware text read that detects the .enc variant on disk, decrypts
#      via core.at_rest_encryption.decrypt_text when an EncryptionConfig
#      is supplied, returns None on any failure shape (defensive — all
#      callers already had None-fallback paths).
#    - master_key: Optional[bytes] = None field on ToolDispatcher
#      dataclass.
#    - __post_init__ builds self._enc_config = EncryptionConfig(master_key)
#      once at construction so the hot path doesn't allocate per dispatch.
#    - All 6 module-level helpers that previously read
#      constitution_path.read_text directly now accept an
#      encryption_config kwarg and route through _read_constitution_text:
#        * _load_initiative_level
#        * _load_constitution_mcp_allowlist
#        * _load_resolved_constraints
#        * _apply_provider_posture_overrides
#        * _hardware_quarantine_reason
#        * _reality_anchor_opt_out
#    - Pipeline-step wiring uses functools.partial to bind
#      self._enc_config to the loader_fn references at construction
#      time. ConstraintResolutionStep + InitiativeFloorStep +
#      PostureOverrideStep + the HardwareQuarantineStep + the
#      RealityAnchorStep opt-out reader all carry the config.
#    - Direct method invocations of the helpers (line ~961 mcp
#      allowlist; line ~2191 + 2213 resolved_for_genre +
#      post-approval counter pre-check) pass self._enc_config.
#
# 2. daemon/deps.py: ToolDispatcher constructor now receives
#    master_key=getattr(app.state, "master_key", None). Single env
#    var (FSF_AT_REST_ENCRYPTION) gates every consumer.
#
# 3. daemon/routers/tool_dispatch.py: _load_agent_default_provider
#    gains encryption_config kwarg; the call site reads
#    request.app.state.master_key and threads it through.
#
# 4. daemon/routers/conversation_helpers.py: read_ambient_opt_in
#    gains encryption_config; routes through _read_constitution_text.
#
# 5. daemon/routers/conversations.py: ambient_nudge route builds
#    EncryptionConfig from request.app.state.master_key and passes
#    it to _read_ambient_opt_in.
#
# Tests (test_dispatcher_constitution_encryption.py — 13 cases):
#   - _read_constitution_text plaintext + encrypted + missing +
#     encrypted-no-config-defensive
#   - _load_initiative_level encrypted + wrong-config defaults to L5
#   - _load_constitution_mcp_allowlist encrypted
#   - _load_resolved_constraints encrypted
#   - _apply_provider_posture_overrides encrypted
#   - _hardware_quarantine_reason encrypted (no binding → None)
#   - _reality_anchor_opt_out encrypted (explicit + default-in)
#   - All loaders against plaintext + no config → bit-identical
#     pre-T5b behavior (regression guard)
#
# What's NOT in T5b (queued for T6 / B273):
#
# - Keychain + passphrase prompt UX. T5b assumes the master key is
#   already on app.state.master_key (lifespan does the resolution).
#   T6 ships the operator UX for resolving it on first boot when
#   nothing is in the Keychain yet.
# - End-to-end /agents/{id}/tools/call integration test that births
#   an agent under encryption + dispatches a tool. Deferred to the
#   integration phase after T6 makes the bootstrap UX work.
#
# Why this is its own burst (not part of T5):
# §0 Hippocratic gate discipline. T5a was the write side + immediate
# operator-facing reads. T5b is a distinct read-side surgery touching
# the dispatcher hot path. Splitting keeps each commit a single
# coherent change — easier to review, bisect, revert.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/tools/dispatcher.py \
        src/forest_soul_forge/daemon/deps.py \
        src/forest_soul_forge/daemon/routers/tool_dispatch.py \
        src/forest_soul_forge/daemon/routers/conversation_helpers.py \
        src/forest_soul_forge/daemon/routers/conversations.py \
        tests/unit/test_dispatcher_constitution_encryption.py \
        dev-tools/commit-bursts/commit-burst272-adr0050-t5b-dispatcher-encryption.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(security): ADR-0050 T5b — dispatcher constitution reads encryption-aware (B272)

Burst 272. Closes the gap left by T5a (B271). T5a shipped the
write side + write-adjacent reads (birth + voice rewrite +
character_sheet + ingest). T5b ships the dispatcher hot-path
constitution.yaml reads so encrypted agents successfully dispatch
tools end-to-end.

What changes:

  - tools/dispatcher.py grows a _read_constitution_text helper
    that detects the .enc variant on disk, decrypts via
    core.at_rest_encryption.decrypt_text when an EncryptionConfig
    is supplied, and returns None on any failure shape (all
    callers already had None-fallback paths so the failure mode
    surfaces as 'use the safe default' rather than a crash).

  - ToolDispatcher gains master_key: Optional[bytes] = None.
    __post_init__ constructs self._enc_config once so the hot
    path doesn't allocate an EncryptionConfig per dispatch.

  - All 6 module-level constitution readers gain an
    encryption_config kwarg threaded through _read_constitution_text:
    _load_initiative_level, _load_constitution_mcp_allowlist,
    _load_resolved_constraints, _apply_provider_posture_overrides,
    _hardware_quarantine_reason, _reality_anchor_opt_out.

  - Pipeline-step wiring uses functools.partial to bind
    self._enc_config to the loader_fn references at construction.
    HardwareQuarantineStep + ConstraintResolutionStep +
    InitiativeFloorStep + PostureOverrideStep + the
    RealityAnchorStep opt-out reader all carry the config.

  - Direct method invocations of the helpers (mcp allowlist
    union, post-approval resolved_for_genre + counter pre-check)
    pass self._enc_config.

  - daemon/deps.py wires master_key from app.state into the
    ToolDispatcher constructor. Single env var
    (FSF_AT_REST_ENCRYPTION) gates every encryption consumer.

  - daemon/routers/tool_dispatch.py: _load_agent_default_provider
    threads encryption_config; route reads
    request.app.state.master_key.

  - daemon/routers/conversation_helpers.py: read_ambient_opt_in
    threads encryption_config; routes through
    _read_constitution_text. conversations.ambient_nudge passes
    EncryptionConfig built from app.state.master_key.

Tests: test_dispatcher_constitution_encryption.py — 13 cases.

After T5b, ADR-0050's encrypted-data-tier closure is complete:
audit chain (T3), registry (T2), memory body (T4), soul +
constitution files (T5a + T5b). Remaining tranches T6-T8 ship
the operator UX (Keychain prompt + passphrase fallback), the
runbook, and the fsf encrypt CLI."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 272 complete — ADR-0050 T5b shipped ==="
echo "Encrypted-data-tier closure: chain (T3) + registry (T2) +"
echo "memory (T4) + soul/const files (T5a+T5b) = all four sealed."
echo "Remaining: T6 (Keychain UX), T7 (runbook), T8 (CLI)."
echo ""
echo "Press any key to close."
read -n 1
