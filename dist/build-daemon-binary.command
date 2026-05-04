#!/usr/bin/env bash
# Build the standalone daemon binary via PyInstaller.
#
# ADR-0042 T4 (Burst 101). Operator runs this once to produce the
# binary the Tauri desktop shell bundles in production builds.
# After this completes, `cargo tauri build` from apps/desktop/
# can include the binary as a sidecar resource without requiring
# the end user to install Python.
#
# Prerequisites:
#   - Python 3.11+ with the project installed (pip install -e .)
#   - PyInstaller >= 6.0 (the script will install it if absent)
#
# Build time: ~30-60 seconds on a modern Mac.
# Output size: ~50-80MB single-file binary.
# Output path: dist/dist/forest-soul-forge-daemon
#
# After this script completes:
#   1. Copy the binary into the Tauri sidecar location:
#        cp dist/dist/forest-soul-forge-daemon \
#           apps/desktop/binaries/forest-soul-forge-daemon-x86_64-apple-darwin
#      (the `-x86_64-apple-darwin` suffix is Tauri's per-arch
#      naming convention; use aarch64 on Apple Silicon hosts.)
#   2. cd apps/desktop && cargo tauri build
#
# Cross-compilation: not supported. Build once per target arch
# on a matching host. macOS Universal binaries can be produced
# via `lipo` after building x86_64 + aarch64 separately.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
cd "$REPO_ROOT"

echo "=== Forest Soul Forge — daemon binary build ==="
echo

# ---- Verify environment -------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not on PATH." >&2
  echo "Install Python 3.11+ before running this." >&2
  exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
case "$PYTHON_VERSION" in
  3.1[1-9]|3.2[0-9])
    echo "Python: $PYTHON_VERSION ✓"
    ;;
  *)
    echo "ERROR: Python 3.11+ required, found $PYTHON_VERSION" >&2
    exit 1
    ;;
esac

# ---- Verify project installed ------------------------------------------
if ! python3 -c "import forest_soul_forge" 2>/dev/null; then
  echo "ERROR: forest_soul_forge package not installed in current Python."
  echo "Run from repo root: pip install -e ."
  exit 1
fi
echo "forest_soul_forge package: importable ✓"

# ---- Install PyInstaller if missing ------------------------------------
if ! python3 -c "import PyInstaller" 2>/dev/null; then
  echo "Installing PyInstaller >= 6.0 ..."
  python3 -m pip install --upgrade "pyinstaller>=6.0"
fi
PI_VERSION=$(python3 -c 'import PyInstaller; print(PyInstaller.__version__)')
echo "PyInstaller: $PI_VERSION ✓"

# ---- Clean prior build outputs -----------------------------------------
# PyInstaller writes to ./build and ./dist relative to spec dir.
# Since the spec lives in dist/, this writes to dist/build and
# dist/dist. Cleaning only those keeps the surrounding repo state
# untouched.
echo
echo "Cleaning prior build artifacts..."
rm -rf "$HERE/build" "$HERE/dist"

# ---- Run PyInstaller ----------------------------------------------------
echo
echo "Running PyInstaller (~30-60s) ..."
echo
cd "$HERE"
python3 -m PyInstaller \
  --noconfirm \
  --clean \
  daemon-pyinstaller.spec

# ---- Verify output -----------------------------------------------------
BINARY_PATH="$HERE/dist/forest-soul-forge-daemon"
if [ ! -x "$BINARY_PATH" ]; then
  echo "ERROR: expected binary at $BINARY_PATH not found." >&2
  exit 1
fi

BINARY_SIZE=$(du -h "$BINARY_PATH" | cut -f1)
echo
echo "=== Build complete ==="
echo "Binary: $BINARY_PATH"
echo "Size:   $BINARY_SIZE"
echo
echo "Next steps:"
echo "  1. Smoke-test the binary:"
echo "       $BINARY_PATH --port 7424"
echo "     then curl http://127.0.0.1:7424/healthz"
echo "  2. Stage for Tauri bundling:"
echo "       mkdir -p $REPO_ROOT/apps/desktop/binaries"
echo "       cp $BINARY_PATH \\\\"
echo "          $REPO_ROOT/apps/desktop/binaries/forest-soul-forge-daemon-\$(uname -m)-apple-darwin"
echo "  3. Build the Tauri bundle:"
echo "       cd $REPO_ROOT/apps/desktop && cargo tauri build"
echo
read -rp "Press Enter to close..."
