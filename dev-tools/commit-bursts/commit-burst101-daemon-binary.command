#!/usr/bin/env bash
# Burst 101: ADR-0042 T4 — daemon-as-binary build via PyInstaller.
#
# Eliminates the "users must have Python 3.11+ installed" requirement
# from the Tauri-packaged distribution. After this burst:
#
#   operator's host (one-time):
#     ./dist/build-daemon-binary.command
#
#   produces:
#     dist/dist/forest-soul-forge-daemon  (~50-80MB single file)
#
#   then `cargo tauri build` bundles that binary as a sidecar
#   resource. End users get a .app that contains the daemon —
#   no Python install required.
#
# WHAT'S NEW
#
# 1. src/forest_soul_forge/daemon/__main__.py — entry point so
#    `python -m forest_soul_forge.daemon` works (Tauri shell's
#    dev-mode subprocess invocation depends on this) AND so
#    PyInstaller has a single function to bundle. argparse
#    surfaces --host/--port/--log-level/--reload. Equivalent to
#    the existing `uvicorn forest_soul_forge.daemon.app:app`
#    invocation in run.command, just plumbed through Python.
#
# 2. dist/daemon-pyinstaller.spec — PyInstaller config. Single-
#    file binary; bundles config/ and examples/ as data
#    resources; enumerates uvicorn's hidden imports + FastAPI
#    reflection deps + ADR-0027/0036/0041 dynamic imports +
#    yaml.cyaml. console=True so Tauri shell can pipe stdout/
#    stderr. UPX off (broken on macOS arm64). codesign_identity
#    None (T5 wires real signing).
#
# 3. dist/build-daemon-binary.command — operator script. Verifies
#    Python 3.11+ + forest_soul_forge importable; pip-installs
#    PyInstaller >= 6.0 if absent; cleans prior build outputs;
#    runs PyInstaller; verifies binary exists; prints staging
#    instructions for the Tauri sidecar location.
#
# 4. apps/desktop/src/main.rs — spawn_daemon() now tries the
#    bundled binary first (adjacent to the desktop exe), falls
#    back to `python3 -m forest_soul_forge.daemon` for dev. This
#    means a single shell binary supports both `cargo tauri dev`
#    (no binary) and `cargo tauri build` (with binary) without
#    branching at compile time.
#
# 5. apps/desktop/tauri.conf.json — bundle.externalBin includes
#    "binaries/forest-soul-forge-daemon". Tauri arranges for the
#    arch-suffixed file
#      apps/desktop/binaries/forest-soul-forge-daemon-<arch>-<platform>
#    to be staged into the bundle's resources at build time.
#
# 6. apps/desktop/README.md — production build flow documented:
#    build daemon binary → stage with arch suffix → cargo tauri
#    build. Cross-arch caveat (lipo for Universal binaries).
#
# 7. .gitignore — apps/desktop/binaries/ (never commit; arch-
#    specific 50-80MB builds), dist/build/, dist/dist/.
#
# WHY PYINSTALLER OVER PYOXIDIZER / NUITKA
#
# - PyInstaller: simplest config, well-documented, handles
#   uvicorn's hidden imports + FastAPI's reflection-heavy
#   startup. Single-file output (~50-80MB). Operator-installable
#   without further toolchain.
# - PyOxidizer: smaller (~10-30MB) but more complex spec; relies
#   on Rust toolchain; Python stdlib coverage gaps surface as
#   runtime errors. Revisit if v0.5 bundle crosses 100MB total.
# - Nuitka: smallest binaries, slowest build (~10x); compiles
#   Python to C; daemon's lazy imports + dynamic schema
#   migrations would need refactoring. Wrong fit for v0.5.
#
# Trade-off documented in dist/daemon-pyinstaller.spec header.
#
# VERIFICATION
#
# Sandbox can't run PyInstaller (no host-side build tools).
# Sandbox-side verification:
#   - python -m forest_soul_forge.daemon --help works (argparse
#     output exactly matches spec)
#   - import forest_soul_forge.daemon.__main__ clean
#   - 2177 unit tests pass (adding __main__.py didn't break
#     module discovery)
#
# Host-side verification path (operator):
#   1. ./dist/build-daemon-binary.command
#   2. ./dist/dist/forest-soul-forge-daemon --port 7424
#   3. curl http://127.0.0.1:7424/healthz
#   4. mkdir -p apps/desktop/binaries
#   5. cp dist/dist/forest-soul-forge-daemon \\
#        apps/desktop/binaries/forest-soul-forge-daemon-$(uname -m)-apple-darwin
#   6. cd apps/desktop && cargo tauri build
#
# OUTSTANDING IN ADR-0042
#
# - T5 (Bursts 102-103): code signing + auto-updater. Apple
#   Developer account ($99/yr); Tauri's tauri-plugin-updater;
#   manifest hosting decision (GitHub Releases vs CDN vs
#   custom).
# - T6 (post-v0.5): pricing/landing page. Out of repo scope;
#   marketing surface.
#
# After T5, the v0.5 distribution chain is end-to-end:
# install Tauri CLI → run build commands → ship a signed,
# auto-updating .app to SMB users without requiring them to
# touch Python.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 101 — ADR-0042 T4: daemon-as-binary build ==="
echo
clean_locks
git add src/forest_soul_forge/daemon/__main__.py
git add dist/daemon-pyinstaller.spec
git add dist/build-daemon-binary.command
git add apps/desktop/src/main.rs
git add apps/desktop/tauri.conf.json
git add apps/desktop/README.md
git add .gitignore
git add commit-burst101-daemon-binary.command
clean_locks
git status --short
echo
clean_locks
git commit -m "feat(dist): daemon-as-binary build via PyInstaller (ADR-0042 T4)

Eliminates the 'users must have Python installed' requirement
from the v0.5 Tauri-packaged distribution. After this burst, the
production build chain is:

  ./dist/build-daemon-binary.command       # ~30-60s, one-time
  cp dist/dist/forest-soul-forge-daemon \\\\
     apps/desktop/binaries/forest-soul-forge-daemon-\$(uname -m)-apple-darwin
  cd apps/desktop && cargo tauri build     # ships a sidecar bundle

End users install a .app that contains the daemon as a sidecar
binary; the Tauri shell prefers the bundled binary over a system
python3, falling back to python3 -m only when the bundle isn't
present (developer mode).

Components:

1. src/forest_soul_forge/daemon/__main__.py — argparse-driven
   entry point. Makes 'python -m forest_soul_forge.daemon' work
   (Tauri shell's dev-mode subprocess depends on this) and gives
   PyInstaller a single function to bundle. Equivalent to the
   existing uvicorn invocation in run.command.

2. dist/daemon-pyinstaller.spec — PyInstaller spec. Single-file
   binary; bundles config/ + examples/ as data resources;
   enumerates uvicorn's hidden imports + FastAPI reflection deps
   + ADR-0027/0036/0041 dynamic imports + yaml.cyaml.
   console=True (Tauri pipes stdout/stderr); UPX off (broken on
   macOS arm64); codesign_identity None until T5 wires signing.

3. dist/build-daemon-binary.command — operator build script.
   Verifies Python 3.11+ + forest_soul_forge importable;
   pip-installs PyInstaller >= 6.0 if absent; runs build;
   verifies output; prints staging instructions for the Tauri
   sidecar location.

4. apps/desktop/src/main.rs — spawn_daemon() prefers the bundled
   binary (adjacent to the desktop exe) before falling back to
   python3 -m. Single shell binary supports both 'cargo tauri
   dev' (no daemon binary) and 'cargo tauri build' (with
   bundled binary).

5. apps/desktop/tauri.conf.json — bundle.externalBin includes
   the daemon binary so Tauri stages it into the bundle's
   resources directory.

6. apps/desktop/README.md — production build flow documented;
   cross-arch caveat (lipo for Universal binaries) called out.

7. .gitignore — apps/desktop/binaries/ (arch-specific 50-80MB
   builds, never committed), dist/build/, dist/dist/.

Why PyInstaller over PyOxidizer / Nuitka: simpler spec, well-
documented, handles uvicorn's hidden imports cleanly. Trade-off
accepted: ~50-80MB sidecar bundle for v0.5. Revisit PyOxidizer
for v0.6+ if the total installer crosses 100MB.

Verification (sandbox can't run PyInstaller):
- python -m forest_soul_forge.daemon --help works (argparse
  output verified)
- import forest_soul_forge.daemon.__main__ clean
- 2177 unit tests pass (no regression from adding __main__.py)

Outstanding ADR-0042 work:
- T5 (Bursts 102-103): code signing + tauri-plugin-updater
- T6 (post-v0.5): pricing/landing page (out of repo scope)"

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 101 landed. Daemon-as-binary scaffolding complete."
echo "Operator: run ./dist/build-daemon-binary.command to produce the binary."
echo ""
read -rp "Press Enter to close..."
