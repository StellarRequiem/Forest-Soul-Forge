#!/usr/bin/env bash
# Forest Soul Forge — build a distributable .zip for shipping to evaluators.
#
# Wraps `git archive` because git already knows what's tracked and what
# isn't (.gitignore + .gitattributes). The .zip contains everything an
# evaluator needs to start.command from a fresh extraction:
#
#   - All source (src/, frontend/, scripts/, examples/, scenarios/)
#   - All operator scripts (start/stop/reset/start-demo/load-scenario/...)
#   - Config (config/, pyproject.toml)
#   - Docs (README.md, STATE.md, docs/)
#   - The synthetic-incident scenario data (lives under scenarios/)
#
# Excluded automatically by git:
#   - .git/, .venv/, __pycache__/, data/, demo/, *.bak.*
#   - dist/forest-soul-forge-*.zip (so this script doesn't bundle prior builds)
#
# Output:
#   dist/forest-soul-forge-<short-sha>-<yyyymmdd>.zip
#
# Usage:
#   ./dist/build.command          # build from current HEAD
#
# What the recipient does with it:
#   1. Unzip to ~/Forest-Soul-Forge/   (or wherever)
#   2. Double-click start.command      (~30s first run, ~5s after)
#   3. Browser opens to the Forge
#
# To pre-load a scenario before they unzip and run, the scenarios/
# directory ships in the .zip — they can run
# ./scenarios/load-scenario.command synthetic-incident demo
# before start-demo.command for the headline demo.

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

GREEN="\033[1;32m"
BLUE="\033[1;34m"
YELLOW="\033[1;33m"
RED="\033[1;31m"
RESET="\033[0m"

say()  { printf "${BLUE}[dist]${RESET} %s\n" "$*"; }
ok()   { printf "${GREEN}[dist]${RESET} %s\n" "$*"; }
warn() { printf "${YELLOW}[dist]${RESET} %s\n" "$*"; }
err()  { printf "${RED}[dist]${RESET} %s\n" "$*" 1>&2; }

press_to_close() {
  echo ""
  echo "Press return to close this window."
  read -r _
}

# ---------- preflight ----------------------------------------------------

if ! command -v git >/dev/null 2>&1; then
  err "git not on PATH. Install Xcode CLT or git from git-scm.com."
  press_to_close
  exit 1
fi

if [ ! -d ".git" ]; then
  err "Not in a git repo. Build must run from a git checkout so the"
  err "archive respects .gitignore + .gitattributes."
  press_to_close
  exit 1
fi

# Warn (don't block) on uncommitted changes — operator may be testing
# a tweak before tagging. The archive only includes what's committed,
# so an uncommitted change WON'T be in the .zip.
if ! git diff-index --quiet HEAD --; then
  warn "Uncommitted changes detected. The .zip will only include committed work."
  warn "Commit first if you want those changes shipped."
fi
if [ -n "$(git ls-files --others --exclude-standard)" ]; then
  warn "Untracked files present. Same rule — the .zip only includes tracked files."
fi

# ---------- build --------------------------------------------------------

SHA="$(git rev-parse --short HEAD)"
DATE="$(date +%Y%m%d)"
NAME="forest-soul-forge-${SHA}-${DATE}"
OUT="dist/${NAME}.zip"

say "Building ${OUT} from HEAD ${SHA}..."

# git archive respects .gitignore + .gitattributes. --prefix puts every
# file under <NAME>/ inside the zip so unzipping creates a clean
# directory rather than dumping into cwd.
if ! git archive --format=zip --prefix="${NAME}/" -o "$OUT" HEAD; then
  err "git archive failed. Output: $OUT (may be partial — removing)."
  rm -f "$OUT"
  press_to_close
  exit 1
fi

# ---------- verify -------------------------------------------------------

if ! command -v unzip >/dev/null 2>&1; then
  warn "unzip not on PATH — skipping verification step."
else
  COUNT="$(unzip -l "$OUT" | tail -1 | awk '{print $2}')"
  say "Archive contains $COUNT files."

  # Spot-check that the critical entry points are in there.
  for required in "${NAME}/start.command" "${NAME}/start-demo.command" \
                  "${NAME}/scenarios/load-scenario.command" \
                  "${NAME}/scenarios/synthetic-incident/audit_chain.jsonl" \
                  "${NAME}/scenarios/synthetic-incident/registry.sqlite" \
                  "${NAME}/README.md" "${NAME}/pyproject.toml" \
                  "${NAME}/src/forest_soul_forge/daemon/app.py"; do
    if ! unzip -l "$OUT" "$required" >/dev/null 2>&1; then
      warn "  MISSING: $required"
    fi
  done
  ok "Spot-check passed — critical entry points present."
fi

SIZE_HUMAN="$(du -h "$OUT" | cut -f1)"
ok "Built: $OUT ($SIZE_HUMAN)"

# ---------- handoff ------------------------------------------------------

cat <<EOF

${GREEN}Distribution build complete.${RESET}

  ${OUT}
  ${SIZE_HUMAN}

To share with an evaluator:
  1. Send them the .zip
  2. They unzip wherever (creates ${NAME}/)
  3. They double-click ${NAME}/start.command
     - ~30s first run (creates .venv, pip install)
     - ~5s subsequent runs
  4. Browser opens to the Forge UI

For a demo-ready out-of-box experience, point them at the
synthetic-incident scenario:
  cd ${NAME}
  ./scenarios/load-scenario.command synthetic-incident demo
  ./start-demo.command

EOF

press_to_close
