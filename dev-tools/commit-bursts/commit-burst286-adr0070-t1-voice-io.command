#!/bin/bash
# Burst 286 — ADR-0070 T1: voice I/O substrate + canonical local Whisper.
#
# The operator gets voice as a first-class front door. Different
# backends (Whisper-cpp / faster-whisper / OpenAI / ElevenLabs /
# Apple Speech / Piper) plug in via the same shape ADR-0043 used
# for MCP plugins: small interface, swappable backends, local
# default, hosted opt-in.
#
# What ships:
#
# 1. docs/decisions/ADR-0070-voice-io-substrate.md — full record.
#    Five decisions (plugin shape not builtin, local Whisper as
#    canonical default, transcripts are audit chain entries,
#    wake-word + push-to-talk co-exist, /voice/* is its own router).
#    Eight tranches T1-T7.
#
# 2. src/forest_soul_forge/core/voice_io.py:
#    - VoiceTranscript frozen dataclass (text, language, duration_s,
#      confidence, backend_id, model_id, elapsed_ms, optional segments)
#    - VoiceIOError taxonomy (Unavailable / FormatUnsupported /
#      Timeout / Decode — all subclass VoiceIOError for family catch)
#    - VoiceIOProtocol — the interface every voice_io plugin implements.
#      Two RPC methods (transcribe + synthesize); plugins declare via
#      supported_methods which they implement.
#    - LocalWhisperBackend — canonical sovereign default ASR.
#      Wraps whisper-cpp via lazy import (module loads without the
#      optional dep; operators who don't enable voice don't pay the
#      import cost). Model file operator-supplied; setup script
#      (T7) downloads ggml-base.en.bin. Inference adapter stubbed
#      in T1; real binding wiring lands in T2 with the /voice/transcribe
#      HTTP endpoint.
#
# 3. core/audit_chain.py: register three new event types —
#    voice_transcribed, voice_synthesized, voice_failed. Transcripts
#    go in event_data so encryption-at-rest envelope (ADR-0050 T3)
#    covers them when on.
#
# Tests (test_voice_io.py — 9 cases):
#   - VoiceTranscript is frozen (mutation raises)
#   - VoiceTranscript.segments defaults None
#   - Error taxonomy: all subclass VoiceIOError
#   - LocalWhisperBackend declares correct backend_id /
#     supported_methods / supported_input_formats subset of global
#   - transcribe rejects bad audio_format with VoiceFormatUnsupported
#   - transcribe raises VoiceBackendUnavailable on missing model file
#   - synthesize raises (ASR-only backend)
#   - KNOWN_EVENT_TYPES contains all three new voice events
#   - Module imports without pulling whisper-cpp (lazy-import
#     regression guard)
#
# What's NOT in T1 (queued):
#   T2: /voice/transcribe + /voice/synthesize HTTP endpoints +
#       real whisper-cpp binding wiring inside _invoke_transcribe
#   T3: push-to-talk frontend mode in Talk/Chat tab
#   T4: wake-word + always-listening daemon mode
#   T5: forest-voice-piper TTS canonical
#   T6: hosted backend adapters (ElevenLabs, OpenAI Whisper)
#   T7: voice runbook + setup script

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0070-voice-io-substrate.md \
        src/forest_soul_forge/core/voice_io.py \
        src/forest_soul_forge/core/audit_chain.py \
        tests/unit/test_voice_io.py \
        dev-tools/commit-bursts/commit-burst286-adr0070-t1-voice-io.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(voice): ADR-0070 T1 — voice I/O substrate + local Whisper (B286)

Burst 286. First tranche of the voice I/O arc — gives the
operator voice as a first-class front door for the ten-domain
platform. Plugin-shape (not builtin) so different backends
(Whisper-cpp / faster-whisper / OpenAI / ElevenLabs / Apple
Speech / Piper) swap freely while sovereignty defaults stay
local.

What ships:

  - docs/decisions/ADR-0070: 5 decisions (plugin shape;
    local Whisper canonical default; transcripts ARE audit
    chain entries; wake-word + push-to-talk co-exist;
    /voice/* is its own router separate from /conversations).
    8 tranches T1-T7 documented.

  - core/voice_io.py: VoiceTranscript frozen dataclass +
    VoiceIOProtocol (transcribe + synthesize RPC interface) +
    VoiceIOError taxonomy (Unavailable / FormatUnsupported /
    Timeout / Decode) + LocalWhisperBackend canonical
    sovereign default. Whisper-cpp binding lazy-imported so
    module loads without the optional dep — operators not
    enabling voice don't pay the import cost. Inference
    adapter stubbed in T1; real binding wiring lands T2 with
    HTTP endpoints.

  - core/audit_chain.py: register voice_transcribed,
    voice_synthesized, voice_failed in KNOWN_EVENT_TYPES.
    Transcripts in event_data → encryption-at-rest envelope
    (ADR-0050 T3) covers them when on.

Tests: test_voice_io.py — 9 cases covering VoiceTranscript
shape + frozen-ness, error taxonomy inheritance,
LocalWhisperBackend identifier declarations, format gate,
missing-model gate, ASR-only enforcement on synthesize(),
audit event registration, lazy-import regression guard.

Queued T2-T7: HTTP endpoints + binding wiring, push-to-talk
frontend, wake-word, Piper TTS, hosted backends, runbook."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 286 complete — ADR-0070 T1 voice substrate shipped ==="
echo "Next: T2 /voice/* HTTP endpoints + real whisper-cpp wiring."
echo ""
echo "Press any key to close."
read -n 1
