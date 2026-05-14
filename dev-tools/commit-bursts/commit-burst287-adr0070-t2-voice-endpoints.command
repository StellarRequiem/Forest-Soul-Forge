#!/bin/bash
# Burst 287 — ADR-0070 T2: /voice/* HTTP endpoints + real whisper-cpp wiring.
#
# Two endpoints:
#   POST /voice/transcribe — multipart audio upload → VoiceTranscript JSON
#     Audit-chains voice_transcribed; encryption-at-rest covers the raw
#     transcript text via the existing envelope (ADR-0050 T3).
#   GET  /voice/status — backend health: which backend / model_path /
#     model_present + 24h transcribed + failed counts
#
# T5 adds /voice/synthesize. T4 adds wake-word streaming.
#
# What ships:
#
# 1. core/voice_io.py: _invoke_transcribe wiring filled in.
#    pywhispercpp binding shape (preferred — most common). Writes
#    audio_bytes to a tempfile, calls binding.transcribe(path, language).
#    Adapter normalizes pywhispercpp Segment objects (and older
#    versions emitting 10ms units) into {text, start, end,
#    avg_logprob, lang} dicts via _normalize_pywhispercpp_segments.
#
# 2. daemon/routers/voice.py: /voice/transcribe + /voice/status.
#    - Backend cache so whisper-cpp model stays warm across
#      requests (first call ~3-5s on M4 mini; subsequent ~real-time)
#    - VoiceIOError taxonomy → HTTP status mapping:
#        VoiceBackendUnavailable → 503
#        VoiceFormatUnsupported → 415
#        VoiceTimeoutError → 504
#        VoiceDecodeError → 422
#        Other VoiceIOError → 500
#    - Failure path emits voice_failed audit event with reason_code
#    - Success path emits voice_transcribed with full transcript +
#      backend_id + model_id + duration_s + confidence + audio_format +
#      audio_bytes_len
#    - audit append failures are non-fatal — HTTP response still
#      delivers the transcript
#
# 3. daemon/app.py: include voice_router alongside orchestrator
#    + reality_anchor + security.
#
# Tests (test_daemon_voice.py — 9 cases):
#   _normalize_pywhispercpp_segments:
#     - None input returns []
#     - Dict-form (seconds) preserves all fields
#     - Object-form (.text/.start/.end attributes) reads correctly
#     - 10ms-unit detection + auto-conversion to seconds
#     - Text trimmed of whitespace
#   _get_backend:
#     - Caches instance (whisper model stays warm)
#   _transcript_to_dict:
#     - Full marshaling
#     - None segments preserved
#   _count_voice_events_24h:
#     - Window filtering (recent vs 48h-old)
#     - Empty chain
#
# What's NOT in T2:
#   - Frontend push-to-talk in Talk/Chat tab — T3
#   - Wake-word streaming — T4
#   - TTS endpoint (/voice/synthesize) — T5
#   - Hosted backend adapters (ElevenLabs, OpenAI Whisper) — T6
#   - Setup script for ggml-base.en.bin download — T7

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/voice_io.py \
        src/forest_soul_forge/daemon/routers/voice.py \
        src/forest_soul_forge/daemon/app.py \
        tests/unit/test_daemon_voice.py \
        dev-tools/commit-bursts/commit-burst287-adr0070-t2-voice-endpoints.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(voice): ADR-0070 T2 — /voice/* HTTP endpoints (B287)

Burst 287. Operator-facing ASR endpoint + real whisper-cpp
binding wiring. Closes the T1 stub.

What ships:

  - core/voice_io.py: _invoke_transcribe filled in. Wraps
    pywhispercpp binding (preferred) via tempfile + path-based
    transcribe call. _normalize_pywhispercpp_segments adapter
    handles seconds-unit + 10ms-unit segments (auto-detect by
    magnitude), object + dict shapes (binding versions differ),
    text trim.

  - daemon/routers/voice.py: POST /voice/transcribe (multipart
    audio upload, returns VoiceTranscript JSON, audit-chains
    voice_transcribed with full transcript + metadata) + GET
    /voice/status (backend id / model_path / model_present /
    24h activity counts). VoiceIOError taxonomy maps cleanly to
    HTTP codes: backend_unavailable→503, format_unsupported→415,
    timeout→504, decode_error→422, other→500. Audit-append
    failures non-fatal.

  - Backend cache keeps whisper-cpp model warm across requests
    (first call cold ~3-5s on M4 mini; subsequent near real-time).

  - daemon/app.py: include voice_router.

Tests: test_daemon_voice.py — 9 cases covering segment
normalization (None / seconds / object / 10ms units / trim),
backend caching, transcript marshaling (full + None segments),
24h activity counting (window + empty).

Queued T3-T7: frontend push-to-talk, wake-word streaming, TTS
endpoint, hosted backend adapters, model setup script."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 287 complete — ADR-0070 T2 /voice/* endpoints shipped ==="
echo "Next: T3 frontend push-to-talk OR T5 TTS canonical."
echo ""
echo "Press any key to close."
read -n 1
