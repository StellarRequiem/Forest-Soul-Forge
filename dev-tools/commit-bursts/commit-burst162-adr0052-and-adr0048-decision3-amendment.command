#!/bin/bash
# Burst 162 — ADR-0052 (pluggable secrets storage) + ADR-0048
# Decision 3 amendment (three-preset allowance UI). Both come from
# the 2026-05-06 operator-decisions session.
#
# T11 decision (closed): plugin secrets storage is operator-pickable.
#   Three reference backends — macOS Keychain, VaultWarden, plaintext
#   file — plus a BYO module-path for HashiCorp Vault / 1Password
#   Connect / etc. Operator selects via FSF_SECRET_STORE env var.
#   Closes ADR-0043 follow-up #4.
#
# Allowance-tier decision (refines ADR-0048 Decision 3): three preset
#   tiers operators pick from in the Chat-tab settings panel, plus an
#   Advanced disclosure for per-tool grants + trust-tier overrides.
#   Restricted (read tools only) / Specific (per-category) / Full
#   (all 6 tools). Single-click presets cover the common postures;
#   Advanced covers power users.
#
# What ships:
#
#   docs/decisions/ADR-0052-pluggable-secrets-storage.md (~280 lines)
#     - 6 decisions + 6 implementation tranches
#     - SecretStoreProtocol shape (get / put / delete / list_names)
#     - Three reference impls: KeychainStore (macOS), VaultWardenStore
#       (cross-platform HTTPS), FileStore (plaintext + chmod 600 +
#       insecure-warning banner)
#     - BYO module-path: FSF_SECRET_STORE=module:my_pkg.MyStore
#     - Audit-chain events: secret_put, secret_resolved, secret_delete,
#       secret_store_unreachable. Names + backend captured; values
#       NEVER logged.
#     - CLI surface: `fsf secret put|get|delete|list|backend`
#
#   docs/decisions/ADR-0048-computer-control-allowance.md
#     - Decision 3 rewritten: three preset tiers (Restricted / Specific
#       / Full) + Advanced disclosure
#     - Restricted = read-only tools (screenshot + clipboard)
#     - Specific = the original per-category hybrid framing — preserved
#       inside this preset tier
#     - Full = all 6 tools granted; per-tool requires_human_approval
#       still fires per call; posture still clamps
#     - Advanced disclosure: per-tool toggles + trust-tier overrides
#       for power users
#     - Posture interaction clarified: presets define what the agent
#       COULD do; posture defines what it CAN do right now. Full +
#       red = all action tools refused (read still fires). Operators
#       flip to red as global brake without losing grant state.
#
# No code touched in this burst — both pieces are ADR design.
# Implementation tranches queued:
#
#   ADR-0052 T1 — Protocol + FileStore (1 burst)
#   ADR-0052 T2 — KeychainStore (0.5 burst)
#   ADR-0052 T3 — VaultWardenStore (1 burst)
#   ADR-0052 T4 — Loader integration + audit events (0.5 burst)
#   ADR-0052 T5 — `fsf secret` CLI (0.5 burst)
#   ADR-0052 T6 — Settings UI integration (0.5 burst)
#
#   ADR-0048 T2 — Read tools (computer_screenshot + read_clipboard)
#   ADR-0048 T3 — Action tools (4 tools)
#   ADR-0048 T4 — Allowance UI implementing the three-preset Decision 3
#                 (closes ADR-0047 T4 full)
#   ADR-0048 T6 — Documentation + safety guide
#
# Per ADR-0044 D3: zero kernel ABI surface changes in either ADR.
# Both are userspace-only.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0052-pluggable-secrets-storage.md \
        docs/decisions/ADR-0048-computer-control-allowance.md \
        dev-tools/commit-bursts/commit-burst162-adr0052-and-adr0048-decision3-amendment.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(adr): ADR-0052 pluggable secrets + ADR-0048 D3 amendment (B162)

Burst 162. Two ADR pieces from the 2026-05-06 operator-decisions
session:

ADR-0052 (NEW) — pluggable secrets storage. Closes ADR-0043
follow-up #4. SecretStoreProtocol with get/put/delete/list_names;
three reference backends (KeychainStore, VaultWardenStore,
FileStore); BYO via FSF_SECRET_STORE=module:my.pkg.Class. Audit
events capture name + backend without ever logging values. CLI
mirror: fsf secret put|get|delete|list|backend. Six implementation
tranches T1-T6.

ADR-0048 Decision 3 amended — three-preset allowance UI:
- Restricted: read tools only (screenshot + clipboard)
- Specific: per-category toggles (the original hybrid framing
  preserved inside this preset)
- Full: all 6 tools granted
Plus Advanced disclosure for per-tool grants + trust-tier
overrides. Posture interaction clarified: presets define what
the agent COULD do; posture (green/yellow/red) defines what it
CAN do right now. Full preset + red posture = action tools
refused, read tools fire. Operators flip to red as global brake
without losing grant state.

No code touched. Both ADRs userspace-only per ADR-0044 D3.

Implementation queued:
- ADR-0052 T1-T6 (~4-5 bursts)
- ADR-0048 T2/T3/T4/T6 (~3-4 bursts)

Tasks #10/#11/#12/#13 closed in this session as well — see
project_forest_project_family memory entry for the sibling-repo
catalog (buddy / cus-core / blue-team-guardian / collector /
CompanionForge / MouseMates) added during T13 scoping."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 162 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
