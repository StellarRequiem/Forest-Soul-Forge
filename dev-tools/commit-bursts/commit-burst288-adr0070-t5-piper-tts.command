#!/bin/bash
# Burst 288 — ADR-0070 T5: Piper TTS canonical + /voice/synthesize.
#
# Operator's agents can now SPEAK. Symmetric to T1/T2's ASR path:
# local-default, plugin-shape-ready, sovereign by default. Voice
# substrate is now full-duplex (input + output).
#
# What ships:
#
# 1. core/voice_io.py: LocalPiperBackend class.
#    - Wraps piper-tts Python binding via lazy-import (module
#      loads without piper-tts; operators not enabling TTS pay
#      nothing)
#    - voices_dir defaults to ~/.forest/voice-models/piper/
#    - Per-voice cached PiperVoice instances — first synthesize
#      pays ~150ms voice-load cost, subsequent ~real-time
#    - WAV output (Piper's native; 22kHz mono PCM)
#    - available_voices() lists .onnx files for /voice/status
#    - transcribe() raises VoiceBackendUnavailable (TTS-only)
#    - Text bounded: 1-5000 chars
#
# 2. daemon/routers/voice.py:
#    - POST /voice/synthesize — multipart form: text + voice_id
#      (optional) + output_format (wav) + timeout_s. Returns
#      WAV bytes with Content-Type audio/wav + X-Voice-* headers
#      for backend_id / voice_id / elapsed_ms.
#    - Audit-chains voice_synthesized with full text + voice_id +
#      audio_bytes_len + elapsed_ms. Same privacy posture as
#      voice_transcribed — encryption-at-rest envelope covers.
#    - VoiceIOError taxonomy → HTTP status mapping mirrors
#      /voice/transcribe (backend_unavailable→503, etc.)
#    - GET /voice/status extended: now reports BOTH asr {} and
#      tts {} subsections with backend identifiers + paths +
#      available_voices list. activity_24h gets a synthesized
#      counter alongside transcribed/failed.
#
# 3. tests/unit/test_voice_io_piper.py — 10 cases:
#    - identifier declarations (TTS-only backend shape)
#    - transcribe raises VoiceBackendUnavailable
#    - synthesize refuses: empty text, whitespace-only, oversized
#      (>5000), bad output_format, missing voice file
#    - available_voices: empty dir / dir with .onnx + ignored
#      non-.onnx / nonexistent dir
#
# 4. tests/unit/test_daemon_voice.py: _count_voice_events_24h
#    return-tuple extended (transcribed, synthesized, failed).
#    Test updates: synthesized event counted, 3-tuple destructure.
#
# After T5 the voice substrate is functionally complete for
# operator-facing use cases. Push-to-talk frontend (T3) +
# wake-word (T4) + hosted-backend adapters (T6) + setup script
# (T7) remain.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/voice_io.py \
        src/forest_soul_forge/daemon/routers/voice.py \
        tests/unit/test_voice_io_piper.py \
        tests/unit/test_daemon_voice.py \
        dev-tools/commit-bursts/commit-burst288-adr0070-t5-piper-tts.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(voice): ADR-0070 T5 — Piper TTS + /voice/synthesize (B288)

Burst 288. Voice substrate is now full-duplex. Symmetric to
T1/T2's ASR: local-default, plugin-shape-ready, sovereign by
default.

What ships:

  - core/voice_io.py: LocalPiperBackend. Lazy-imports piper-tts
    binding so module load stays cheap. Per-voice cache keeps
    inference near-real-time after the ~150ms first-load.
    available_voices() lists operator-installed .onnx files.
    Text bounded 1-5000; output WAV-only in T5.

  - daemon/routers/voice.py: POST /voice/synthesize multipart
    endpoint returns WAV bytes with X-Voice-* response headers
    (backend_id, voice_id, elapsed_ms). Audit-chains
    voice_synthesized with full text + audio_bytes_len.
    VoiceIOError → HTTP status mapping mirrors transcribe.
    GET /voice/status extended: asr {} + tts {} subsections +
    activity_24h.synthesized counter.

  - _count_voice_events_24h returns 3-tuple now (transcribed,
    synthesized, failed). Test signatures updated.

Tests: test_voice_io_piper.py — 10 cases for TTS-only enforcement,
text bounds, format gate, missing-voice gate, available_voices
listing. test_daemon_voice.py updated for 3-tuple counts +
synthesized window-filter case.

Queued T3/T4/T6/T7: push-to-talk frontend, wake-word streaming,
hosted backend adapters, model+voice setup script."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 288 complete — ADR-0070 T5 TTS shipped ==="
echo "Voice substrate is full-duplex. Operator can talk + agents can speak."
echo ""
echo "Press any key to close."
read -n 1
