#!/usr/bin/env bash
# Forest Soul Forge — bootstrap + launch (the "first time you run this" path).
#
# Double-click from Finder. Brings the stack up from a clean checkout:
#   1. Verify Python ≥ 3.11 is on PATH
#   2. Create .venv if missing  (via `python3 -m venv .venv`)
#   3. `pip install -e .` if forest_soul_forge isn't importable yet
#   4. Hand off to run.command which starts daemon + frontend foreground
#
# After the first successful run, the venv stays put and re-runs are
# fast (skip steps 2-3). For day-to-day "the venv exists, just bring it
# up" use, run.command is still the right shortcut. start.command is
# the safe entry point for evaluators / new contributors.
#
# Reset to a clean state with reset.command. Stop a running stack with
# stop.command (or Ctrl-C inside the run.command terminal window).

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

BLUE="\033[1;34m"
GREEN="\033[1;32m"
YELLOW="\033[1;33m"
RED="\033[1;31m"
DIM="\033[2m"
RESET="\033[0m"

say()  { printf "${BLUE}[start]${RESET} %s\n" "$*"; }
ok()   { printf "${GREEN}[start]${RESET} %s\n" "$*"; }
warn() { printf "${YELLOW}[start]${RESET} %s\n" "$*"; }
err()  { printf "${RED}[start]${RESET} %s\n" "$*" 1>&2; }

press_to_close() {
  echo ""
  echo "Press return to close this window."
  read -r _
}

# ---------- step 1: Python version check ---------------------------------

PY_REQUIRED_MAJOR=3
PY_REQUIRED_MINOR=11

if ! command -v python3 >/dev/null 2>&1; then
  err "python3 not found on PATH."
  err "Install from https://www.python.org/downloads/  (Python ${PY_REQUIRED_MAJOR}.${PY_REQUIRED_MINOR}+ required)"
  press_to_close
  exit 1
fi

PY_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || echo "0.0")"
PY_MAJOR="${PY_VERSION%%.*}"
PY_MINOR="${PY_VERSION##*.}"

if [ "$PY_MAJOR" -lt "$PY_REQUIRED_MAJOR" ] \
   || { [ "$PY_MAJOR" -eq "$PY_REQUIRED_MAJOR" ] && [ "$PY_MINOR" -lt "$PY_REQUIRED_MINOR" ]; }; then
  err "Python ${PY_VERSION} found, but ${PY_REQUIRED_MAJOR}.${PY_REQUIRED_MINOR}+ is required."
  err "Update from https://www.python.org/downloads/"
  press_to_close
  exit 1
fi
ok "Python ${PY_VERSION} detected."

# ---------- step 2: virtualenv -------------------------------------------

if [ ! -x ".venv/bin/python" ]; then
  say "No .venv yet — creating one (~5 seconds)..."
  if ! python3 -m venv .venv; then
    err "Failed to create .venv. Make sure 'python3 -m venv' works on your machine."
    press_to_close
    exit 1
  fi
  ok ".venv created."
else
  ok ".venv already present."
fi

# Always ensure pip is current. Old pip+resolver pairings produce confusing
# errors during the editable install on first run.
say "Upgrading pip in the venv (quiet)..."
.venv/bin/python -m pip install --quiet --upgrade pip || warn "pip upgrade reported a warning — continuing."

# ---------- step 3: editable install -------------------------------------

if .venv/bin/python -c "import forest_soul_forge.daemon.app" >/dev/null 2>&1; then
  ok "forest_soul_forge already importable."
else
  say "Installing forest_soul_forge (editable, ~30 seconds first time)..."
  if ! .venv/bin/pip install --quiet -e .; then
    err "pip install -e . failed."
    err "Try manually:  .venv/bin/pip install -e ."
    press_to_close
    exit 1
  fi
  if ! .venv/bin/python -c "import forest_soul_forge.daemon.app" >/dev/null 2>&1; then
    err "Install reported success but the package still won't import."
    err "Check pyproject.toml or report a bug."
    press_to_close
    exit 1
  fi
  ok "forest_soul_forge installed."
fi

# ---------- step 4: hand off to run.command ------------------------------

if [ ! -x "run.command" ]; then
  err "run.command missing or not executable. Repo may be incomplete."
  press_to_close
  exit 1
fi

ok "Bootstrap complete. Handing off to run.command..."
echo ""

# Use exec so this terminal session BECOMES run.command — Ctrl-C still
# works for clean shutdown, and we don't leave a parent process around.
exec ./run.command
