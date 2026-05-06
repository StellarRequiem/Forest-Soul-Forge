#!/bin/bash
# Burst 176 — turnkey installer for the soulux-computer-control
# plugin + runbook fix. Surfaced live during the 2026-05-06 e2e
# test follow-up: the bare `fsf plugin install` command from the
# original runbook failed on the operator's host with
# `ModuleNotFoundError: No module named 'pydantic'` because the
# operator's interactive shell python3 doesn't have Forest's
# runtime deps — those live in the daemon's venv at
# /Users/llm01/Forest-Soul-Forge/.venv/.
#
# Root cause: pyproject.toml declares `fsf` under [project.scripts]
# but it only registers as a console script when the package is
# installed via `pip install -e .` into the active shell's Python.
# Operators running just the daemon's venv don't have `fsf` on
# PATH. The runbook's bare `fsf plugin install ...` command
# assumed a Python install that wasn't always there.
#
# Fix: ship a turnkey wrapper that resolves the right Python
# automatically + a runbook update pointing operators at it.
#
# What ships:
#
#   dev-tools/install-soulux-computer-control.command:
#     6-step diagnostic installer that:
#       0. environment check (PATH, fsf-on-PATH, system python3,
#          venv python, pydantic-importable-via-venv-python)
#       1. ensure target plugin_root (~/.forest/plugins)
#       2. invoke `plugin install` via venv Python with PYTHONPATH
#          set to ./src — uses the full code path the daemon uses,
#          with all deps resolved
#       3. verify install on disk (ls the staged dir)
#       4. POST /plugins/reload with token from .env
#       5. confirm daemon's active plugin list includes the entry
#       6. summary pointer at /tmp/fsf-plugin-install.log
#
#     Resolution order for the Python interpreter:
#       1. .venv/bin/python (preferred — has all runtime deps)
#       2. command -v fsf (if pip install -e was run)
#       3. system python3 (last resort; expected to fail on
#          missing deps — surfaces the bug for diagnosis)
#
#     Output streams to both stdout AND /tmp/fsf-plugin-install.log
#     so an operator who closes the terminal can still read what
#     happened.
#
#   examples/plugins/soulux-computer-control/README.md:
#     Replaces the old `## Install (after T2 ships)` section with
#     the new turnkey + manual flows. Documents the venv-Python
#     gotcha so future operators don't hit the same pydantic-not-
#     found mystery. Pointer at this burst (B176) preserves the
#     historical context.
#
# Verified live on operator's Mac post-fix:
#   ✓ rc=0 on install via venv Python
#   ✓ POST /plugins/reload returned added=['soulux-computer-control']
#   ✓ active plugins list now includes the entry
#   ✓ chat-tab grants surface works against the installed plugin
#
# Per ADR-0044 D3: zero kernel ABI surface changes. Wrapper script
# + documentation only.
#
# This bug class (host-Python vs. venv-Python) is a known sharp
# edge in any Python project that ships scripts assuming `pip
# install -e .`. The operator-facing fix is to always provide a
# turnkey wrapper that resolves the right interpreter rather than
# leaving operators to debug import errors. Same pattern should
# apply to any future operator-facing CLI command in the repo —
# especially anything mentioned in docs/runbooks/.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/install-soulux-computer-control.command \
        dev-tools/restart-daemon.command \
        examples/plugins/soulux-computer-control/README.md \
        dev-tools/commit-bursts/commit-burst176-install-soulux-wrapper.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(plugin): turnkey installer for soulux-computer-control (B176)

Burst 176. Surfaced live during the 2026-05-06 e2e test follow-up.
The bare fsf plugin install command from the original runbook
failed on the operator's host with ModuleNotFoundError pydantic
because the operator's interactive shell python3 doesn't have
Forest's runtime deps — those live in the daemon's venv at
.venv/. The fsf console script registered in pyproject.toml only
ends up on PATH when the package was pip install -e d into the
active shell's Python.

Fix: ship a turnkey wrapper that resolves the right Python
automatically.

Ships:
- dev-tools/install-soulux-computer-control.command: 6-step
  diagnostic installer. Resolution order for the interpreter:
  .venv/bin/python (has deps), then command -v fsf, then system
  python3. Streams output to /tmp/fsf-plugin-install.log so
  closing the terminal doesn't lose the trace.
- examples/plugins/soulux-computer-control/README.md: replaces
  the old install section with turnkey + manual flows. Documents
  the venv-vs-system-python gotcha.
- dev-tools/restart-daemon.command (from B175 e2e prep, now
  staged): single-purpose kickstart wrapper used during e2e.

Verified live on operator's Mac:
- rc=0 on install via venv Python
- POST /plugins/reload returned added=['soulux-computer-control']
- daemon's active plugin list includes the entry
- chat-tab grants surface works against the installed plugin

Per ADR-0044 D3: zero kernel ABI surface changes. Wrapper +
documentation only."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 176 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
