#!/bin/bash
# Burst 146 — Phase 3 design ADRs: ADR-0047 (Persistent Assistant
# Chat) + ADR-0048 (Computer Control Allowance).
#
# Closes T20 + T21 design phase. No code yet — just ADRs locking the
# design decisions before implementation tranches start.
#
# What ships:
#
#   docs/decisions/ADR-0047-persistent-assistant-chat.md
#     Single-agent persistent chat mode for the Chat tab. Backed by
#     one dedicated agent born once per operator, holding state in a
#     single long-lived conversation room. Strictly additive —
#     multi-agent rooms (ADR-003Y) stay unchanged. ZERO kernel ABI
#     impact (uses existing conversation runtime + memory subsystem
#     + posture + grants).
#
#     Decision 1: userspace-only delivery
#     Decision 2: agent source — new dedicated agent, born on first use
#     Decision 3: persistence — single long-lived conversation per operator
#     Decision 4: coexistence — additive to multi-agent rooms
#     Decision 5: settings panel exposes posture + allowances
#
#     6 implementation tranches (T1-T6), 4-5 bursts total.
#
#   docs/decisions/ADR-0048-computer-control-allowance.md
#     SoulUX MCP plugin (`soulux-computer-control`) providing macOS
#     automation tools (screenshot, click, type, run_app, etc.) +
#     Chat-tab allowance UI (hybrid: friendly category toggles
#     backed by per-tool grants). ZERO kernel ABI impact.
#
#     Decision 1: userspace-only via MCP plugin
#     Decision 2: 6 initial tools (2 read-only + 4 external)
#     Decision 3: hybrid allowance UI (categories + per-tool)
#     Decision 4: posture clamps — red dominates grants
#     Decision 5: per-call approval flow via existing queue
#     Decision 6: audit-chain visibility via standard events
#
#     6 implementation tranches (T1-T6), 5-6 bursts total.
#
# Both ADRs cross-reference each other (ADR-0047's settings panel
# exposes ADR-0048's allowances; ADR-0048's posture clamps build on
# ADR-0045 which ADR-0047 also touches).
#
# Phase 3 design done. Implementation arcs queued — operator-side
# call on which tranche to run first.
#
# Per-ADR-0044 boundary check: NEITHER ADR adds a kernel ABI surface.
# Both ADRs are userspace work that exercises existing kernel
# primitives. The seven v1.0 ABI surfaces (tool dispatch protocol,
# audit chain schema, plugin manifest schema v1, constitution.yaml
# schema, HTTP API contract, CLI surface, schema migrations) all
# remain unchanged.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0047-persistent-assistant-chat.md \
        docs/decisions/ADR-0048-computer-control-allowance.md \
        dev-tools/commit-bursts/commit-burst146-adrs-0047-0048.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(adr): ADR-0047 + ADR-0048 — assistant chat + computer control (B146)

Burst 146. Closes T20 + T21 design phase. Pure design ADRs locking
the decisions before implementation tranches start. No code.

Ships:

- ADR-0047 — Persistent Assistant Chat Mode (single-agent).
  Adds a Persistent Assistant mode to the Chat tab, backed by one
  dedicated agent born once per operator, holding state in a single
  long-lived conversation room. Strictly additive — multi-agent
  rooms (ADR-003Y) stay unchanged. ZERO kernel ABI impact.

  Decisions: (1) userspace-only delivery, (2) agent source = new
  dedicated agent born on first use, (3) persistence = single
  long-lived conversation per operator, (4) coexistence with
  multi-agent rooms, (5) settings panel exposes posture +
  allowances. 6 implementation tranches, ~4-5 bursts.

- ADR-0048 — Computer Control Allowance for Assistant.
  SoulUX MCP plugin (soulux-computer-control) providing macOS
  automation tools (screenshot, click, type, run_app, etc.) +
  Chat-tab allowance UI (hybrid: friendly category toggles backed
  by per-tool grants). ZERO kernel ABI impact.

  Decisions: (1) userspace-only via MCP plugin, (2) 6 initial
  tools, (3) hybrid allowance UI, (4) posture clamps red>grants,
  (5) per-call approval via existing queue, (6) audit-chain
  visibility via standard events. 6 implementation tranches,
  ~5-6 bursts.

Both ADRs cross-reference each other and parallel the kernel/
userspace boundary discipline from ADR-0044. Neither touches the
seven v1.0 kernel ABI surfaces.

Phase 3 design done. Implementation arcs queued for operator's
call on tranche ordering. Sets up the 'another outside analysis'
follow-on arc (per the operator's stated direction) with concrete
designs to evaluate."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 146 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
