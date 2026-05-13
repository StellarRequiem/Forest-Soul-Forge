#!/bin/bash
# Burst 260 — pyproject numpy declaration gap.
#
# Pre-existing gap surfaced during the post-B259 session
# validation: 4 test modules
#   tests/unit/test_procedural_shortcuts.py
#   tests/unit/test_procedural_shortcut_dispatch.py
#   tests/unit/test_procedural_embedding.py
#   tests/unit/test_memory_tag_outcome.py
# import numpy, but numpy was not declared anywhere in
# pyproject.toml. The 4 modules died at collection time with
# ModuleNotFoundError so they never ran. Two source modules
# (procedural_embedding.py + procedural_shortcuts.py) require
# numpy in production for the ADR-0054 procedural-shortcut
# substrate.
#
# Fix shape mirrors existing pattern (browser/daemon/conformance
# extras): a NEW [procedural] extra declares numpy as the
# substrate's runtime dep, AND [dev] now includes numpy so the
# standard test command collects the whole suite without
# ModuleNotFoundError.
#
# Substrate is still OFF by default per ADR-0054 (T6 UI not yet
# wired). Operators enabling the substrate today install with
# `pip install forest-soul-forge[procedural]`.
#
# No code changes; pyproject edit only.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add pyproject.toml \
        dev-tools/commit-bursts/commit-burst260-pyproject-numpy-gap.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "build(pyproject): declare numpy for ADR-0054 substrate (B260)

Burst 260. Pre-existing pyproject gap surfaced during the
post-B259 full-suite validation: 4 unit-test modules failed
collection with ModuleNotFoundError because they import the
procedural-shortcut substrate code, which depends on numpy,
but numpy was not declared anywhere in pyproject.

Fix: add [procedural] optional-dependencies group with
numpy>=1.26 (matches the browser/daemon/conformance pattern —
substrate-specific deps live in their own extras), and add
numpy>=1.26 to [dev] so the standard test command can collect
the whole suite without an import error.

Substrate still OFF by default per ADR-0054 — T6 chat-thumbs
UI not yet wired. Operators enabling the substrate today
install with: pip install forest-soul-forge[procedural]
and flip the substrate flag in daemon config.

No code changes; pyproject edit only. Verify by re-running
the full pytest collection — the 4 test modules should now
collect cleanly (still subject to their own runtime asserts)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 260 complete — numpy declared, dev install needs reinstall ==="
echo "Run:  pip install -e '.[dev]'   to pick up numpy in the active venv."
echo "Press any key to close."
read -n 1
