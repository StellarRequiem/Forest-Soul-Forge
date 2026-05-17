#!/bin/bash
# Burst 358 - capture B350 + B353 lessons in CLAUDE.md so future
# sessions internalize them as project conventions, not discoveries.
#
# Two new sections in CLAUDE.md's "Operating principles":
#
# §2 Dispatcher wiring discipline — B350 lesson:
#    Every subsystem the dispatcher claims to expose via
#    ToolContext needs THREE things or it's silently dead code:
#      1. typed field on ToolContext (base.py)
#      2. population line in dispatcher.py:999 constructor
#      3. probe in section-06-ctx-wiring's SUBSYSTEMS list
#    Missing any one = tool passes unit tests (fixture builds
#    ctx by hand) but raises on HTTP path. audit_chain_verify
#    was dead since ADR-0033 Phase B1 until D3 Phase A surfaced
#    it.
#
# §3 Bare version strings — B353 lesson:
#    _VERSION = "1" not "v1". The registry key composer at
#    base.py:_key does f"{name}.v{version}". Pre-fixed "v1"
#    produces .vv1 keys, fails tool_runtime startup_diagnostic.
#    Every other builtin uses the bare form; mirror exactly.
#
# Pure documentation — no code change. The harness section 04
# already catches the §3 drift mechanically; this just makes the
# rule explicit so future-me adding a new builtin tool doesn't
# re-derive the convention from grepping.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add CLAUDE.md \
        dev-tools/commit-bursts/commit-burst358-claude-md-wiring-and-version-discipline.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(claude.md): §2 wiring discipline + §3 version strings (B358)

Burst 358. Capture B350 + B353 lessons in CLAUDE.md so future
sessions internalize them as project conventions, not as
discoveries to re-derive.

§2 Dispatcher wiring discipline (B350):
  Every subsystem the dispatcher claims to expose via
  ToolContext needs three things or its silently dead code:
    1. typed field on ToolContext in tools/base.py
    2. population line in dispatcher.py:999 constructor call
    3. probe in section-06-ctx-wiring SUBSYSTEMS list
  Missing any one = tool passes unit tests (fixture builds ctx
  by hand) but raises ToolValidationError on the HTTP path.
  audit_chain_verify was dead since ADR-0033 Phase B1 until D3
  Phase A live verification surfaced it.

§3 Bare version strings (B353):
  _VERSION must be \"1\" not \"v1\". The registry composer
  builds the key as name.v + version; pre-fixed v1 produces
  .vv1 keys that mismatch the catalog and trip the tool_runtime
  startup_diagnostic. Every other builtin uses the bare numeric
  form; mirror exactly.

Pure documentation. Harness section 04 catches §3 drift
mechanically; this makes the rule explicit so adding a new
builtin tool doesnt require re-deriving the convention via grep."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 358 complete - CLAUDE.md updated ==="
echo ""
echo "Press any key to close."
read -n 1 || true
