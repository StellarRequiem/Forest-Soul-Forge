#!/bin/bash
# Tag v0.5.0-rc — checkpoint for the implementation-complete
# portion of the v0.5 arc.
#
# What this tag marks:
#   - ADR-0042 v0.5 Product Direction: T1 doc + T2 responsive +
#     T3.1 Tauri shell + T4 PyInstaller binary all shipped.
#     T5 (signing + auto-updater) DEFERRED, gated on the Apple
#     Developer account decision.
#   - ADR-0043 MCP-First Plugin Protocol: T1 design + T2 manifest
#     schema + fsf plugin CLI + T3 daemon runtime + /plugins
#     endpoints + T4 audit-chain integration + T4.5 dispatcher
#     bridge + T5 canonical examples + contribution guide all
#     shipped. Implementation-complete.
#
# What's NOT in this tag (why it's -rc, not final):
#   - ADR-0042 T5 Tauri code-signing + sparkle-style auto-updater
#     (gated on Apple Developer cost/posture decision)
#   - ADR-0043 deferred follow-ups:
#     - per-tool requires_human_approval mirroring (currently
#       flips per-server bool when ANY tool requires approval)
#     - allowed_mcp_servers auto-grant flow
#     - frontend Tools-tab plugin awareness (so plugin-contributed
#       servers visually distinguish from YAML-registered ones)
#     - plugin_secret_set audit event (no secrets surface yet)
#
# v0.5.0 final supersedes this tag once the Apple Developer
# decision is made AND the deferred follow-ups land. Same shape
# as v0.4.0-rc → v0.4.0 (Burst 92 → Burst 95).
#
# Test suite: 2,289 passing / 3 skipped / 1 xfailed (32.38s).
# Schema unchanged at v13.

set -euo pipefail

cd "$(dirname "$0")"

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

# Confirm we're on the right commit before tagging.
echo "--- HEAD ---"
git log --oneline -1
echo ""

# Reject if HEAD isn't the doc-refresh commit. The tag belongs
# on top of the STATE/CHANGELOG refresh.
HEAD_SUBJECT=$(git log -1 --pretty=format:'%s')
if [[ "$HEAD_SUBJECT" != docs:* ]]; then
  echo "WARN: HEAD subject is '$HEAD_SUBJECT' — expected the docs refresh."
  echo "Continuing anyway, but verify this is intentional."
fi

# Check we don't already have v0.5.0-rc.
if git rev-parse v0.5.0-rc >/dev/null 2>&1; then
  echo "ERROR: tag v0.5.0-rc already exists. Delete it first if re-tagging."
  exit 3
fi

git tag -a v0.5.0-rc -m "v0.5.0-rc — ADR-0042 + ADR-0043 implementation-complete checkpoint

Checkpoint marking the implementation-complete portion of the
v0.5 arc. Two new Accepted ADRs, both with their core tranches
shipped:

ADR-0042 v0.5 Product Direction (Burst 97):
  T1 doc                                Burst 97
  T2 frontend responsive pass           Burst 98
  T3.1 Tauri 2.x shell scaffolding      Burst 99
  T4 daemon-as-binary (PyInstaller)     Burst 101
  T5 signing + auto-updater             DEFERRED

ADR-0043 MCP-First Plugin Protocol (Burst 103):
  T1 design                             Burst 103
  T2 manifest schema + fsf plugin CLI   Burst 104
  T3 daemon runtime + /plugins HTTP     Burst 105
  T4 audit-chain integration            Burst 106
  T4.5 dispatcher bridge                Burst 107
  T5 examples + contribution guide      Burst 108
  Per-tool approval mirroring           DEFERRED
  allowed_mcp_servers auto-grant        DEFERRED
  Frontend Tools-tab plugin awareness   DEFERRED
  plugin_secret_set audit event         DEFERRED

Numbers (verified against disk on 2026-05-04):
  Test suite      2,289 passing (was 2,177 at v0.4.0; +112)
  Source LoC      48,760 (was 44,648 at v0.4.0; +~4,100)
  ADRs filed      40 files / 38 unique numbers
  Commits on main 264 (was 250 at v0.4.0; +14)
  .command scripts 120 (was 107 at v0.4.0; +13)
  Audit chain      1,118 entries
  Audit event types 67 (was 62 at v0.4.0; +5 plugin lifecycle)
  Schema version   v13 (unchanged)

v0.5.0 final supersedes this tag once Apple Developer signing
decision lands and the ADR-0043 deferred follow-ups ship.
Pattern mirrors v0.4.0-rc (Burst 92) → v0.4.0 (Burst 95).
"

echo ""
echo "--- tag created ---"
git tag --list 'v0.5.*' --sort=-creatordate

echo ""
echo "--- pushing tag to origin ---"
git push origin v0.5.0-rc

echo ""
echo "=== v0.5.0-rc tagged + pushed ==="
echo "Press any key to close this window."
read -n 1
