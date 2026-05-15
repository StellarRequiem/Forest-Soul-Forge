#!/bin/bash
# Burst 327 - ADR-0070 T4: wake-word substrate.
#
# Always-listening daemon mode that watches the mic for a
# configurable phrase. Off by default; operator opts in via
# FSF_WAKE_WORD_ENABLED=true + restart. Continuous mic capture
# is privacy-sensitive — the default posture is OFF.
#
# What ships:
#
# 1. src/forest_soul_forge/core/voice_wake_word.py (NEW):
#    - WakeWordBackend Protocol: start/stop/current_phrase/
#      is_running/backend_id surface.
#    - WakeWordDetection frozen dataclass.
#    - NullWakeWordBackend — substrate-only, never fires.
#      Default backend so the lifespan code path is exercised
#      even without optional deps installed.
#    - OpenWakeWordBackend — reference detector wrapping the
#      openwakeword + sounddevice packages (lazy-imported on
#      start so daemon boots without them). Worker thread
#      polls 1280-sample frames at 16kHz, fires the callback
#      when confidence >= threshold (default 0.5).
#    - resolve_wake_word_backend(backend_id=None, phrase=None):
#      kwarg > env var > default null. Unknown backend falls
#      back to null so the daemon stays up.
#    - wake_word_enabled(): operator's opt-in switch.
#
# 2. src/forest_soul_forge/core/audit_chain.py:
#    KNOWN_EVENT_TYPES extended with voice_wake_word_armed +
#    voice_wake_word_detected + voice_wake_word_disarmed.
#
# 3. src/forest_soul_forge/daemon/app.py:
#    Lifespan constructs + starts the detector when
#    FSF_WAKE_WORD_ENABLED=true. The on-detection callback
#    audit-chains voice_wake_word_detected with the phrase +
#    confidence + backend_id. WakeWordError from a missing
#    native dep is non-fatal — daemon stays up, /healthz
#    reports the failure. Lifespan finally stops the detector
#    and audit-chains voice_wake_word_disarmed.
#
# Tests (test_voice_wake_word.py - 19 cases):
#   Factory (9): default→null, explicit null, off/disabled
#   aliases, openwakeword returned without starting, unknown
#   falls back to null, phrase resolution kwarg/env/default,
#   env backend picked up
#   wake_word_enabled (3): default off, true variants, false
#   variants
#   NullWakeWordBackend (3): is_running default false, start/
#   stop toggle (callback never fires by contract), phrase
#   echoes
#   Audit event registration (3, parametrized): all three
#   event types in KNOWN_EVENT_TYPES
#   Detection (1): frozen dataclass
#
# Sandbox-verified 19/19 pass.
#
# === ADR-0070 progress: 5/6 tranches closed (T1+T2+T3+T4+T5) ===
# Next: T6 hosted-backend adapter.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/voice_wake_word.py \
        src/forest_soul_forge/core/audit_chain.py \
        src/forest_soul_forge/daemon/app.py \
        tests/unit/test_voice_wake_word.py \
        dev-tools/commit-bursts/commit-burst327-adr0070-t4-wake-word.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(voice): ADR-0070 T4 - wake-word substrate (B327)

Burst 327. Always-listening daemon mode that watches the mic
for a configurable phrase. Off by default; operator opts in via
FSF_WAKE_WORD_ENABLED=true + restart. Continuous mic capture is
privacy-sensitive — default posture is OFF.

What ships:

  - core/voice_wake_word.py (NEW): WakeWordBackend Protocol +
    WakeWordDetection frozen dataclass + two implementations.
    NullWakeWordBackend is the substrate-only default (never
    fires; safe to wire). OpenWakeWordBackend wraps the
    optional openwakeword + sounddevice packages, lazy-imports
    them on start so the daemon boots without them. Worker
    thread polls 1280-sample frames at 16kHz; fires callback
    when confidence >= threshold (default 0.5).
    resolve_wake_word_backend() handles kwarg > env > default
    null resolution; unknown ids fall back to null.

  - core/audit_chain.py: KNOWN_EVENT_TYPES extended with
    voice_wake_word_armed / _detected / _disarmed.

  - daemon/app.py: lifespan constructs + starts the detector
    when FSF_WAKE_WORD_ENABLED=true. On-detection callback
    audit-chains voice_wake_word_detected with phrase +
    confidence + backend_id. Lifespan finally stops cleanly
    and audit-chains voice_wake_word_disarmed.

Tests: test_voice_wake_word.py — 19 cases covering 9 factory
branches (default null, explicit null, off/disabled aliases,
openwakeword construction without starting, unknown fallback,
phrase kwarg/env/default, env-backend pickup), 3 wake_word_
enabled scenarios, 3 NullWakeWordBackend behaviors (default
not-running, start/stop toggle with callback-never-fires
invariant, phrase echo), 3 parametrized audit event
registrations, 1 detection-frozen guard. Sandbox-verified
19/19 pass.

ADR-0070 progress: 5/6 closed (T1+T2+T3+T4+T5). Next: T6
hosted-backend adapter (the last tranche)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 327 complete - ADR-0070 T4 wake-word shipped ==="
echo "ADR-0070: 5/6 tranches closed."
echo ""
echo "Press any key to close."
read -n 1
