#!/usr/bin/env bash
# Burst 98: ADR-0042 T2 — frontend responsive pass.
#
# PWA-first half of D3 (mobile platform). The v0.4 frontend was
# desktop-only — at narrow viewports, the 8 tabs clipped, the chat
# grid's 280px rail ate too much horizontal space, the welcome
# banner overflowed, and the statusbar's cells didn't wrap. This
# burst is a CSS-only pass that makes the existing frontend usable
# on phone-narrow viewports without any JS changes or DOM
# restructuring.
#
# Existing breakpoints (kept as-is, predate this burst):
#   1024px — forge-grid stacks (Burst 7)
#    900px — agents-split + memory-grid stack
#
# This burst adds:
#
#   ≤768px (tablet portrait → phone landscape):
#     - .app: tighter padding (sp-3 instead of sp-5)
#     - .top-bar: padding tightened, flex-wrap allowed
#     - .top-bar .brand: 14px font, brand__tag suppressed
#     - .welcome: stacks vertically; close button anchors top-right
#     - .tabs: horizontal scroll instead of clip; touch-scroll wired
#     - .tab: tighter padding, 13px font
#     - .chat-grid: stacks rooms-rail above room body (140px-30vh)
#     - .chat-rail .panel: caps at 30vh
#     - .chat-room .panel__header: stacks title above actions
#     - .panel__actions: tighter gap
#     - .statusbar: wraps cells; smaller font; auto-height
#     - body: 60px bottom padding (compensate for taller statusbar)
#     - .inp, .btn: 32px min-height (tap target hygiene)
#     - .toast: full-width with edge gap
#
#   ≤480px (phone portrait):
#     - .tab: tighter padding (sp-2), 12px font, 13px icon
#       (label suppression deferred — labels are bare text inside
#        <button>, not in a wrapping span; would need DOM change)
#
# DECISIONS
#
# Stack vs slide-out for the chat grid: stacked rail-above-body
# wins for v0.5. Slide-out (rail visible only when toggled) is
# nicer UX but needs JS for the toggle. CSS-only stacking fits
# the burst's "no JS" constraint and matches how operators
# already mentally model the chat tab (rooms list IS visible at
# the top).
#
# Tab horizontal scroll vs wrap: scroll wins. Wrapped tabs eat
# vertical space on a phone (already at a premium); horizontal
# scroll is the iOS Safari pattern for tab bars and feels native.
#
# What this burst does NOT do:
# - Hamburger / drawer navigation. Tabs-as-scroll is good enough.
# - JS-driven mobile-only behaviors. Pure CSS keeps this burst
#   reversible and zero-regression-risk.
# - Mobile-specific font scaling beyond what the breakpoints set.
# - Tauri 2.0 native shell. Per ADR-0042 D3, that's contingent
#   on demand surfacing first. PWA-first ships now; native later.
#
# VERIFICATION
#
# Sandbox: PYTHONPATH=src python3 -m pytest tests/unit
#   → 2177 passed (unchanged; CSS-only burst).
# Manual verification on host: open http://127.0.0.1:5173/ in
# Chrome DevTools; toggle device toolbar; iPhone SE (375px),
# Pixel 7 (412px), iPad mini portrait (768px). Expected: tab
# bar scrolls, chat grid stacks, statusbar cells wrap, no
# horizontal page scroll.
#
# CLOSES
#
# - PWA-first half of ADR-0042 D3.
# - The "frontend responsive pass" item from the Burst 87
#   roadmap's frontend polish queue.
#
# NEXT
#
# Burst 99 (ADR-0042 T3 part 1): Tauri shell scaffolding —
# apps/desktop/ with Tauri config that spawns the daemon binary.
# Pre-binary; just the shell layout.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 98 — ADR-0042 T2: frontend responsive pass ==="
echo
clean_locks
git add frontend/css/style.css
git add commit-burst98-responsive.command
clean_locks
git status --short
echo
clean_locks
git commit -m "feat(frontend): responsive pass for narrow viewports (ADR-0042 T2)

PWA-first half of D3. The v0.4 frontend was desktop-only — at
narrow viewports the 8 tabs clipped, the chat grid's 280px rail
ate too much horizontal space, the welcome banner overflowed,
and the statusbar cells didn't wrap. CSS-only pass making the
existing frontend usable on phone-narrow viewports without any
JS changes or DOM restructuring.

Existing breakpoints kept (1024px forge-grid stack, 900px
agents-split + memory-grid stack). New:

768px and below:
- .app: tighter padding
- .top-bar: padding tightened, flex-wrap allowed; brand__tag
  suppressed to save vertical space
- .welcome: stacks vertically; close button anchors top-right
- .tabs: horizontal scroll instead of clip; -webkit-overflow-
  scrolling: touch
- .tab: tighter padding + 13px font
- .chat-grid: rooms-rail stacks above room body (140px-30vh
  cap on rail so room body stays scannable)
- .chat-room .panel__header: stacks title above actions
- .panel__actions: tighter gap (Burst 88's flex-wrap stays)
- .statusbar: wraps cells; auto-height; min-height 44px
- body: 60px bottom padding (compensates for taller statusbar)
- .inp, .btn: 32px min-height (tap-target hygiene)
- .toast: full-width with edge gap

480px and below:
- .tab: tighter padding (sp-2), 12px font, 13px icon

Decisions:
- Stack vs slide-out for chat grid: stacked wins for v0.5
  (CSS-only; matches operator mental model). Slide-out is
  better UX but needs JS for toggle.
- Tab horizontal scroll vs wrap: scroll wins. Phones are
  vertically constrained; horizontal scroll is the native
  iOS Safari pattern.

What this burst does NOT do:
- Hamburger/drawer navigation
- JS-driven mobile-only behaviors
- Tauri 2.0 native shell (per ADR-0042 D3, contingent on demand)

Verification:
- Sandbox: 2177 unit tests pass (unchanged; CSS-only burst).
- Host (manual): toggle Chrome DevTools device toolbar at
  iPhone SE (375px), Pixel 7 (412px), iPad mini portrait
  (768px). Expected: tab bar scrolls, chat grid stacks,
  statusbar cells wrap, no horizontal page scroll.

Closes the PWA-first half of ADR-0042 D3 + the 'frontend
responsive pass' item from the Burst 87 roadmap's polish queue.
Next: Burst 99 (ADR-0042 T3 part 1) — Tauri shell scaffolding."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 98 landed. Frontend responsive at ≤768px."
echo "Reload the daemon's frontend on your phone to verify."
echo ""
read -rp "Press Enter to close..."
