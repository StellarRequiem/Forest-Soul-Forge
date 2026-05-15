#!/bin/bash
# Burst 326 - ADR-0070 T3: push-to-talk voice frontend.
#
# Wires the existing /voice/transcribe (T2 / B287) + /voice/
# synthesize (T5 / B288) endpoints into a Voice tab in the
# frontend. Hold-to-record via MediaRecorder, transcript renders
# in the pane, audit-chain entry id is surfaced for traceability.
#
# What ships:
#
# 1. frontend/index.html:
#    - New Voice tab (button + svg mic icon).
#    - Voice panel with three sections:
#      * Backend status (refreshable) — ASR backend id + model
#        present check, TTS voice count, 24h activity counts.
#      * Push-to-talk hold-button + transcript display. Space
#        bar also triggers when the Voice tab is active.
#      * TTS composer (textarea + speak button + <audio>).
#
# 2. frontend/js/voice.js (NEW):
#    initVoicePane() controller. MediaRecorder captures
#    webm/opus (or browser default) on mousedown/touchstart/
#    Space, POSTs the blob to /voice/transcribe on release,
#    renders the transcript + audit_chain_entry_id. TTS path
#    POSTs to /voice/synthesize, plays the returned audio.
#    Stream is released after every recording (no zombie
#    mic indicator).
#
# 3. frontend/js/app.js:
#    Imports + initializes the voice pane in both boot
#    branches (success + failure).
#
# Sandbox verification: HTML/JS syntax-check pass; no
# unit-test coverage layer for browser-side MediaRecorder
# (deferred to playwright/cypress, out of scope for the
# T3 closure).
#
# === ADR-0070 progress: 4/6 tranches closed (T1+T2+T3+T5) ===
# Next: T4 wake-word substrate, T6 hosted-backend adapter.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add frontend/index.html \
        frontend/js/voice.js \
        frontend/js/app.js \
        dev-tools/commit-bursts/commit-burst326-adr0070-t3-ptt.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(voice): ADR-0070 T3 - push-to-talk frontend (B326)

Burst 326. Wires the existing /voice/transcribe (T2 / B287) +
/voice/synthesize (T5 / B288) endpoints into a new Voice tab.
Hold-to-record via MediaRecorder, transcript + audit_chain_entry_id
render in the pane for operator traceability.

What ships:

  - frontend/index.html: new Voice tab with three sections —
    backend status (refreshable), push-to-talk hold-button +
    transcript display (Space bar also triggers when tab
    active), TTS composer with textarea + speak button + audio
    playback element.

  - frontend/js/voice.js (NEW): initVoicePane() controller.
    MediaRecorder captures webm/opus on mousedown/touchstart/
    Space; releases POST the blob to /voice/transcribe and
    render the transcript with audit_chain_entry_id. TTS path
    POSTs to /voice/synthesize, plays returned audio. Mic
    stream is released after every recording.

  - frontend/js/app.js: voicePane.initVoicePane() in both
    boot branches.

Browser-side MediaRecorder coverage deferred to a playwright/
cypress layer; T3 closure is the substrate wiring + UX. The
existing backend endpoints already have their own test
coverage (B287 + B288 suites).

ADR-0070 progress: 4/6 closed (T1+T2+T3+T5). Next: T4 wake-
word substrate, T6 hosted-backend adapter."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 326 complete - ADR-0070 T3 PTT shipped ==="
echo "ADR-0070: 4/6 tranches closed."
echo ""
echo "Press any key to close."
read -n 1
