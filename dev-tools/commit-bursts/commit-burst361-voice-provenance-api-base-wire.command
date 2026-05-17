#!/bin/bash
# Burst 361 - voice.js + provenance.js route through api.js so they
# inherit API_BASE resolution + X-FSF-Token auth.
#
# Bug shape (surfaced during the wire-readiness recon, not by the
# diagnostic harness because section-13 hits the daemon directly):
#   voice.js had 3 raw fetch("/voice/...") call sites:
#     /voice/transcribe (POST, multipart)
#     /voice/status (GET, JSON)
#     /voice/synthesize (POST, multipart, returns audio blob)
#   provenance.js had 2 raw fetch("/provenance/...") call sites:
#     /provenance/active (GET, JSON)
#     /provenance/handoffs (GET, JSON)
#   Every other pane in the frontend routes through api.js which
#   resolves `?api=http://127.0.0.1:7423` into the URL. These five
#   bypass it so the requests went to port 5173 (the static
#   frontend server), which 404'd them. Voice + Provenance tabs
#   were dead in the standard dev configuration where frontend is
#   served separately from the daemon.
#
# Fix shape:
#
#   frontend/js/api.js:
#     Add a `multipart()` helper for file-upload calls. Same
#     base-URL + token plumbing as request(), but takes a
#     FormData body and doesn't set Content-Type (the browser
#     sets it with the right multipart boundary). Optional
#     `expectBinary: true` returns the raw Response so the caller
#     can .blob() for voice/synthesize's audio payload.
#
#   frontend/js/voice.js:
#     Import { api, multipart, ApiError } from "./api.js".
#     /voice/transcribe -> multipart("/voice/transcribe", fd)
#     /voice/status     -> api.get("/voice/status")
#     /voice/synthesize -> multipart("/voice/synthesize", fd,
#                                    { expectBinary: true })
#     Error branches reshape to ApiError typing so consumers see
#     status code + body uniformly.
#
#   frontend/js/provenance.js:
#     Import { api, ApiError }.
#     /provenance/active   -> api.get("/provenance/active")
#     /provenance/handoffs -> api.get("/provenance/handoffs")
#     Error branches unify on ApiError.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: Voice + Provenance tabs broken in the standard
#     ?api=... dev configuration. The harness didn't catch this
#     because section-13 hits the daemon directly rather than
#     replicating the frontend's URL resolution; B366's browser-
#     driven smoke (queued) catches this class going forward.
#   Prove non-load-bearing: pure URL plumbing change. Tab logic
#     (recording, playback, render) unchanged. API surface (the
#     daemon endpoints) unchanged.
#   Prove alternative is strictly better: alternatives are
#     (1) prepend API_BASE manually in each call site - shorter
#         diff but two more raw-fetch sites for future authors
#         to copy.
#     (2) make the daemon serve the frontend (mount StaticFiles) -
#         conflates two concerns and breaks the no-cache static
#         server pattern.
#     (a) multipart() helper is the right shape: future file-upload
#         needs (image uploads, etc.) get the same plumbing for
#         free, and the audit lens (token auth) is uniform.
#
# Verification after this commit lands:
#   1. Open the frontend at http://127.0.0.1:5173?api=http://127.0.0.1:7423
#   2. Click Voice tab - status loads (was: stuck on 'loading…').
#   3. Click Provenance tab - precedence ladder + preferences +
#      learned rules + handoffs render.
#   4. Push-to-talk a short phrase - transcript renders.
#   5. Type text + click speak - audio plays.
#
# B366 (browser-driven smoke) will land a screenshot+OCR check that
# catches this class automatically on future regressions.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add frontend/js/api.js \
        frontend/js/voice.js \
        frontend/js/provenance.js \
        dev-tools/commit-bursts/commit-burst361-voice-provenance-api-base-wire.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(frontend): voice + provenance through api.js (B361)

Burst 361. Five raw fetch() call sites (3 in voice.js + 2 in
provenance.js) bypassed api.js's API_BASE resolution, so the
Voice and Provenance tabs were dead in the standard
?api=http://127.0.0.1:7423 dev configuration - requests hit the
static frontend server on 5173 and 404'd.

frontend/js/api.js:
  New multipart() helper - same base-URL + token plumbing as
  request(), takes FormData body, doesn't set Content-Type so
  the browser sets multipart boundary. expectBinary: true
  returns the raw Response for audio/blob payloads.

frontend/js/voice.js:
  /voice/transcribe -> multipart()
  /voice/status     -> api.get()
  /voice/synthesize -> multipart({expectBinary: true})
  Errors unify on ApiError typing.

frontend/js/provenance.js:
  /provenance/active   -> api.get()
  /provenance/handoffs -> api.get()
  Errors unify on ApiError.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: 2 tabs dead in standard dev config; harness
    didn't catch (section-13 hits daemon directly).
  Prove non-load-bearing: URL plumbing only; tab logic + API
    surface unchanged.
  Prove alternative is better: multipart helper is reusable
    (future file-upload tabs inherit it).

After this lands:
  - Open frontend at ?api=...; Voice + Provenance both load.
  - B366 browser-driven smoke (queued) catches this class
    automatically on future regressions."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 361 complete - voice + provenance wired ==="
echo "=========================================================="
echo "Test:"
echo "  Open http://127.0.0.1:5173?api=http://127.0.0.1:7423"
echo "  Click Voice tab - status loads."
echo "  Click Provenance tab - content renders."
echo ""
echo "Press any key to close."
read -n 1 || true
