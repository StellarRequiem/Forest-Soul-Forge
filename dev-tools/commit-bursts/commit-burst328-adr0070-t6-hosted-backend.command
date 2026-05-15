#!/bin/bash
# Burst 328 - ADR-0070 T6: hosted-backend adapter (CLOSES ADR-0070).
#
# Reference hosted ASR adapter (OpenAIWhisperBackend) + stub TTS
# adapter (ElevenLabsTTSBackend). Closes ADR-0070 6/6. Local-
# first remains the canonical default per D2; hosted backends
# are explicit opt-in via env var + secret + agent grant.
#
# What ships:
#
# 1. src/forest_soul_forge/core/voice_io_hosted.py (NEW):
#    - OpenAIWhisperBackend: hosted ASR via OpenAI Whisper API.
#      Lazy-imports the openai package; missing dep surfaces as
#      VoiceBackendUnavailable. Credentials NEVER come from env
#      directly — secret_fn callable resolves through the Forest
#      secrets accessor (ADR-003X C1). Audit chain records
#      backend_id='openai-whisper' so operators trace when hosted
#      ASR was used vs the local default. Timeout errors map to
#      VoiceTimeoutError; other API errors map to VoiceIOError.
#    - ElevenLabsTTSBackend: stub that documents the shape +
#      raises VoiceBackendUnavailable on synthesize so wiring it
#      doesn't silently fall through. Real network synthesis
#      deferred to a future tranche (operator-consent-gated).
#    - resolve_hosted_asr_backend(backend_id, ...): factory used
#      by lifespan's optional backend swap. openai_whisper /
#      openai-whisper / openai aliases all resolve. Unknown id
#      raises VoiceIOError.
#
# Tests (test_voice_io_hosted.py - 20 cases):
#   OpenAIWhisperBackend (13):
#     metadata (1), unsupported format (1), empty audio (1),
#     missing secret_fn (1), empty secret (1), secret_fn raising
#     wrapped (1), missing openai package surfaces unavailable
#     (1), happy path with mocked client (1), missing duration
#     gracefully (1), timeout error mapping (1), generic API
#     error mapping (1)
#   ElevenLabsTTSBackend (2):
#     synthesize is stub, available_voices is empty
#   Factory (5):
#     openai aliases (parametrized 5×), model_id+secret_name
#     propagation, unknown backend raises
#
# Sandbox-verified 20/20 pass.
#
# === ADR-0070 CLOSED 6/6 ===
# Voice I/O substrate arc complete. Phase α scorecard: 8/10
# closed (0050, 0067, 0068, 0070, 0071, 0073, 0074, 0075, 0076).
# Only ADR-0072 still partial (T1+T2+T3 shipped; T4 orchestrator
# integration + T5 frontend pane queued for B329/B330).

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/voice_io_hosted.py \
        tests/unit/test_voice_io_hosted.py \
        dev-tools/commit-bursts/commit-burst328-adr0070-t6-hosted-backend.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(voice): ADR-0070 T6 - hosted-backend adapter (B328) — ARC CLOSED 6/6

Burst 328. Reference hosted ASR adapter (OpenAIWhisperBackend) +
stub TTS adapter (ElevenLabsTTSBackend). Local-first remains the
canonical default per D2; hosted backends are explicit opt-in.

What ships:

  - core/voice_io_hosted.py (NEW): OpenAIWhisperBackend wraps
    OpenAI's audio.transcriptions endpoint. Lazy-imports openai
    so daemons without the dep boot cleanly. Credentials flow
    through a secret_fn callable (production: ctx.secrets.get;
    tests: stub lambda) — NEVER directly from env. Audit chain
    records backend_id='openai-whisper' so operators trace
    hosted vs local use. Timeout errors map to VoiceTimeoutError;
    other API errors map to VoiceIOError. ElevenLabsTTSBackend
    is a stub that documents the shape + refuses cleanly so
    accidental wiring doesn't silently no-op.
    resolve_hosted_asr_backend(backend_id, ...) is the factory
    for the lifespan's backend-swap path; openai_whisper /
    openai-whisper / openai aliases all resolve; unknown id
    raises VoiceIOError.

Tests: test_voice_io_hosted.py — 20 cases covering 13 OpenAI
backend branches (metadata, format/empty/secret refusals,
missing-openai package surfaces VoiceBackendUnavailable, happy
path with mocked client, missing-duration graceful handling,
timeout/error mapping), 2 ElevenLabs stub guards, 5 factory
scenarios (parametrized aliases, kwarg propagation, unknown
rejection). Sandbox-verified 20/20 pass.

=== ADR-0070 CLOSED 6/6 ===
Voice I/O substrate arc complete.

Phase α scorecard: 8/10 closed (0050, 0067, 0068, 0070, 0071,
0073, 0074, 0075, 0076). Only ADR-0072 still partial (T1+T2+T3
shipped; T4 orchestrator integration + T5 frontend pane queued
for B329/B330)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 328 complete - ADR-0070 CLOSED 6/6 ==="
echo "Phase alpha: 8/10 scale ADRs closed."
echo ""
echo "Press any key to close."
read -n 1
