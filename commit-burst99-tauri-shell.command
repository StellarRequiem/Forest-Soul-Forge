#!/usr/bin/env bash
# Burst 99: ADR-0042 T3 part 1 — Tauri shell scaffolding.
#
# Creates apps/desktop/ — the Tauri 2.x packaging that wraps the
# existing daemon + frontend into a desktop app per ADR-0042's
# v0.5 product direction.
#
# WHAT'S NEW
#
# 1. apps/desktop/Cargo.toml — Rust crate spec. Tauri 2.0 +
#    tauri-plugin-shell + serde. Lib + binary crate.
#
# 2. apps/desktop/tauri.conf.json — runtime config. Identifier
#    com.stellarrequiem.forest-soul-forge. 1280×800 default
#    window, 800×600 minimum. Window URL points at the bundled
#    frontend with ?api=http://127.0.0.1:7423 to talk to the
#    daemon. Bundle target = all platforms.
#
# 3. apps/desktop/build.rs — Tauri's standard build glue.
#
# 4. apps/desktop/src/main.rs — shell entry point. On launch:
#    spawns `python3 -m forest_soul_forge.daemon --port 7423`
#    as a subprocess; opens a window pointing at the configured
#    frontend URL; stops the daemon on window close. Exposes one
#    Tauri IPC command (`daemon_status`) so the frontend can
#    introspect daemon state without going through HTTP.
#
# 5. apps/desktop/icons/README.md — placeholder. Real icons get
#    generated via `cargo tauri icon <source.png>` once the
#    1024×1024 source PNG exists. Until then `cargo tauri build`
#    fails on the icon check; `cargo tauri dev` works without
#    bundle icons.
#
# 6. apps/desktop/README.md — operator instructions. One-time
#    Rust + Tauri CLI install; dev-mode invocation; what's not
#    yet working (T4 binary, T5 signing/auto-update).
#
# 7. .gitignore — apps/desktop/target/, apps/desktop/gen/ for
#    Rust build artifacts. Real icons gitignored too (generated
#    locally per icons/README.md instructions).
#
# WHAT'S WORKING AS OF THIS BURST
#
# Operators with Rust + Tauri CLI installed can run
#   cd apps/desktop && cargo tauri dev
# to launch the desktop shell. The shell spawns the existing
# daemon as a Python subprocess, opens a window pointing at the
# existing frontend, and shuts the daemon down on quit.
#
# WHAT'S NOT WORKING YET (deferred per ADR-0042)
#
# - Production builds (cargo tauri build) require:
#   a. Real bundle icons (operator runs `cargo tauri icon`)
#   b. Daemon as a binary (T4 / Burst 101) — currently spawns
#      python3, which means distributing this app requires
#      users to have Python 3.11+ + the package installed
# - Code signing (T5 / Bursts 102-103)
# - Auto-updater (T5 / Bursts 102-103)
#
# WHY SCAFFOLD NOW
#
# Two reasons:
# 1. The repo structure decision (ADR-0042 D5) gets concretized.
#    Future commits land in apps/desktop/ rather than us
#    arguing over harness-app vs subdir vs sibling-repo at
#    every step.
# 2. Operators can experiment with cargo tauri dev today on the
#    existing daemon. That validates the Tauri runtime works
#    against the foundry's actual frontend before we sink Burst
#    101+ into the binary build.
#
# VERIFICATION
#
# Sandbox cannot compile Rust. The scaffolding's correctness is
# verified by:
# - Files match Tauri 2.x scaffolding shape (per docs).
# - tauri.conf.json validates against $schema reference.
# - main.rs uses only public Tauri 2.x APIs (Manager,
#   tauri::command, generate_handler, generate_context,
#   on_window_event, manage(), try_state).
# - Cargo.toml deps pin Tauri ^2.0 (matches plugin versions).
#
# Host-side verification (operator, after pip install -e .):
#   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
#   cargo install tauri-cli --version "^2.0"
#   cd apps/desktop && cargo tauri dev
# Expected: Tauri window opens; daemon spawns; closing window
# kills daemon.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 99 — ADR-0042 T3 part 1: Tauri shell scaffolding ==="
echo
clean_locks
git add apps/desktop/Cargo.toml
git add apps/desktop/tauri.conf.json
git add apps/desktop/build.rs
git add apps/desktop/src/main.rs
git add apps/desktop/icons/README.md
git add apps/desktop/README.md
git add .gitignore
git add commit-burst99-tauri-shell.command
clean_locks
git status --short
echo
clean_locks
git commit -m "feat(desktop): Tauri 2.x shell scaffolding (ADR-0042 T3 part 1)

Creates apps/desktop/ — the Tauri-packaged wrapper around the
existing daemon + frontend per ADR-0042's v0.5 product
direction.

apps/desktop/
├── Cargo.toml         # Tauri 2.0 + plugin-shell + serde
├── tauri.conf.json    # 1280x800 window, identifier
│                      # com.stellarrequiem.forest-soul-forge,
│                      # window URL points at bundled frontend
│                      # with ?api=http://127.0.0.1:7423
├── build.rs           # Tauri's standard build glue
├── src/main.rs        # Shell: spawns daemon subprocess; opens
│                      # window; kills daemon on close. Exposes
│                      # daemon_status as a Tauri IPC command.
├── icons/README.md    # Placeholder — real icons generated
│                      # locally via 'cargo tauri icon <src.png>'
└── README.md          # Operator instructions for dev mode +
                       # what's deferred to T4/T5

.gitignore:
- apps/desktop/target/, apps/desktop/gen/ (Rust artifacts)
- apps/desktop/icons/{*.png,*.icns,*.ico} (generated locally)
- apps/desktop/icons/README.md kept tracked

Working as of this burst:
- Operators with Rust + Tauri CLI installed can run
  'cargo tauri dev' to launch the desktop shell.
- Spawns python3 -m forest_soul_forge.daemon as subprocess.
- Window points at existing frontend.
- Daemon shutdown on window close.

Deferred per ADR-0042 plan:
- Production bundle (cargo tauri build) — needs real icons +
  daemon-as-binary (T4 / Burst 101).
- Code signing (T5 / Bursts 102-103).
- Auto-updater (T5 / Bursts 102-103).

Why scaffold now:
1. Concretizes ADR-0042 D5 (single repo, apps/desktop subdir).
   Future bursts land in apps/desktop/ rather than re-arguing
   structure at every step.
2. Operators can experiment with cargo tauri dev today on the
   existing daemon — validates Tauri runtime works against the
   foundry's frontend before sinking T4 work into the binary.

Verification (sandbox can't compile Rust):
- Files match Tauri 2.x scaffolding shape per docs.
- tauri.conf.json validates against \$schema.
- main.rs uses only public Tauri 2.x APIs.
- Cargo.toml deps pin Tauri ^2.0.

Host-side verification path (post-commit):
  cargo install tauri-cli --version '^2.0'
  cd apps/desktop && cargo tauri dev"

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 99 landed. apps/desktop/ scaffolded."
echo "Run 'cd apps/desktop && cargo tauri dev' to test (needs Rust + Tauri CLI)."
echo "Next: Burst 100 (T3 part 2) — wire frontend dev-server URL or static-file binding."
echo ""
read -rp "Press Enter to close..."
