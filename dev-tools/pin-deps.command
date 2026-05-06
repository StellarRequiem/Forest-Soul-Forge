#!/usr/bin/env bash
# Forest Soul Forge — pin transitive dependencies with hashes.
#
# B150 (T26 security hardening). Uses pip-tools (pip-compile) to
# generate hash-pinned requirements files from pyproject.toml's
# loose >= constraints. Each file lists every transitive dep at an
# exact version with its sha256 hash, so a future install verifies
# the dep tree is exactly what was tested.
#
# Outputs (gitignored intermediate; checked-in lockfiles):
#   requirements.txt          ← pinned core deps (project.dependencies)
#   requirements-daemon.txt   ← pinned daemon extras
#   requirements-dev.txt      ← pinned dev extras (pytest etc.)
#   requirements-browser.txt  ← pinned browser extras (Playwright)
#   requirements-conformance.txt ← pinned conformance-test extras
#
# Auto-installs pip-tools into the active venv if missing.
#
# When to re-run: any time pyproject.toml dependencies change. Commit
# the regenerated requirements*.txt as part of the same change so CI
# (and external integrators) can reproduce the exact dep tree.
#
# CI workflow idea (B150 doesn't ship a workflow file, just the
# generation script): run `pip install -r requirements-daemon.txt`
# in CI instead of `pip install -e ".[daemon]"`. The hash-pin gives
# you reproducible installs + flags upstream-yanked or compromised
# versions.

set -uo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

bar() { printf '\n========== %s ==========\n' "$1"; }

bar "1. preflight: venv + pip-tools"
if [[ ! -x ".venv/bin/python" ]]; then
  echo "  ✗ no .venv/bin/python — run start.command first to bootstrap"
  echo ""; echo "Press return to close."; read -r _; exit 1
fi
PY=".venv/bin/python"
PIP=".venv/bin/pip"

if ! "$PY" -c "import piptools" >/dev/null 2>&1; then
  echo "  installing pip-tools into .venv..."
  "$PIP" install --quiet pip-tools >/dev/null
fi
echo "  ✓ pip-tools available ($("$PY" -c 'import piptools; print(piptools.__version__)'))"

bar "2. generate requirements.txt (core: project.dependencies only)"
"$PY" -m piptools compile \
    --resolver=backtracking \
    --generate-hashes \
    --strip-extras \
    --output-file requirements.txt \
    --quiet \
    pyproject.toml \
  && echo "  ✓ requirements.txt ($(wc -l < requirements.txt | tr -d ' ') lines)" \
  || { echo "  ✗ pip-compile failed"; exit 1; }

bar "3. generate requirements-<extra>.txt for each optional set"
for extra in daemon dev browser conformance; do
  "$PY" -m piptools compile \
      --resolver=backtracking \
      --generate-hashes \
      --strip-extras \
      --extra "$extra" \
      --output-file "requirements-${extra}.txt" \
      --quiet \
      pyproject.toml \
    && echo "  ✓ requirements-${extra}.txt ($(wc -l < requirements-${extra}.txt | tr -d ' ') lines)" \
    || echo "  ! requirements-${extra}.txt failed (extra may not be defined; check pyproject.toml)"
done

bar "4. summary"
echo "  Files generated:"
ls -la requirements*.txt 2>/dev/null | sed 's/^/    /'

cat <<'EOF'

  Commit these files to lock the dep tree. CI can use:
      pip install -r requirements-daemon.txt
  for reproducible builds with hash verification.

  Re-run this script any time pyproject.toml dependencies change.
EOF

echo ""
echo "Done. Press return to close."
read -r _
