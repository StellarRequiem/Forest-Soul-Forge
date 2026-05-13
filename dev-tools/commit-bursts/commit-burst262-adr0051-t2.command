#!/bin/bash
# Burst 262 — ADR-0051 T2: Linux bwrap (bubblewrap) sandbox impl.
#
# T1 (B261) shipped the abstraction + macOS sandbox-exec impl.
# T2 lands the Linux equivalent: bwrap-based sandboxing, the same
# substrate Flatpak uses internally. Same Sandbox Protocol; same
# error-classification taxonomy; different command shape (bwrap
# takes mount-namespace setup as command-line args, no profile file).
#
# === Why bwrap ===
#
# Linux has no sandbox-exec equivalent. The closest options are:
#   - Firejail (depends on a SUID helper — non-starter for the
#     trusted-host security model Forest follows)
#   - nsjail (Google's, less common, fewer distros package it)
#   - bwrap (bubblewrap, used by Flatpak — most-packaged, no SUID,
#     userspace user-namespaces)
#
# bwrap wins on the install-availability + safety axes. Most distros
# ship it as `bubblewrap` in their main repo (apt, dnf, pacman).
# Operators who want sandboxing install bwrap; without it,
# default_sandbox() returns None and the permissive-mode dispatcher
# (T7) falls back to in-process with the audit annotation.
#
# === What's in T2 ===
#
# 1. LinuxBwrap class in src/forest_soul_forge/tools/sandbox.py.
#    Same shape as MacOSSandboxExec (Sandbox Protocol). Differences:
#    - argv-based config (no .sb file); _build_argv() composes the
#      bwrap invocation from a SandboxProfile.
#    - --ro-bind system paths (/usr, /lib, /lib64, /bin, /sbin,
#      /etc, /opt, /var/lib) read-only so the Python interpreter
#      can start. Cross-distro safe via --ro-bind-try (silently
#      skips missing paths).
#    - --proc /proc + --dev /dev + --tmpfs /tmp for required mount
#      points.
#    - --ro-bind for allowed_read_paths, --bind (rw) for
#      allowed_write_paths, --ro-bind-try for allowed_commands.
#    - --unshare-net when allow_network is False (mount-namespace
#      isolation, strictly stronger than allow-rules).
#    - --die-with-parent + --new-session + --unshare-pid for
#      worker hygiene.
#    - Worker tail: '<python> -I -m forest_soul_forge.tools._sandbox_worker'.
#
# 2. default_sandbox() now returns LinuxBwrap on linux when
#    /usr/bin/bwrap exists, None when not.
#
# 3. Tests:
#    - TestLinuxBwrapArgvBuilder (7 cases) — argv shape invariants
#      run on every platform (argv is pure string composition).
#      Covers: bwrap path is argv[0], system ro-binds present,
#      unshare-net behavior, --bind vs --ro-bind for filesystem,
#      worker tail shape, hygiene flags, --unshare-pid.
#    - TestLinuxBwrapSetupFailures (1 case) — patched-binary-missing
#      smoke that runs everywhere.
#    - TestLinuxBwrapIntegration (1 case, skipif-not-linux) — probes
#      user-namespace availability via a 'bwrap --bind / / true' smoke
#      and self-skips if userns is disabled on this host (some
#      hardened RHEL/CentOS deployments).
#
# === Tests expected ===
#
# pre-T2: 53 tests in diag-b261 collection (T1).
# post-T2: 53 + 9 new = 62 tests in collection.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/tools/sandbox.py \
        tests/unit/test_tool_sandbox.py \
        dev-tools/commit-bursts/commit-burst262-adr0051-t2.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(security): ADR-0051 T2 — Linux bwrap sandbox impl (B262)

Burst 262. Linux companion to T1's MacOSSandboxExec. Same
Sandbox Protocol; different command shape (bwrap takes mount-
namespace setup as command-line args, no profile file).

What bwrap brings:
- Mount-namespace isolation: paths NOT bind-mounted into the
  worker's namespace simply don't exist for the worker.
  Structurally stronger than macOS sandbox-exec's allowlist
  rules (no path-traversal trick can reach outside the bound
  set).
- User-namespace + (optional) network-namespace + (always)
  PID-namespace isolation. Worker can only see itself.
- Same substrate Flatpak uses for its sandbox — well-tested,
  packaged on most distros (apt/dnf/pacman: bubblewrap).
- Unprivileged (no SUID) — works for operators without
  root.

LinuxBwrap.run() composes argv via _build_argv():
- --ro-bind-try the minimal Linux system paths (/usr, /lib,
  /lib64, /bin, /sbin, /etc, /opt, /var/lib) read-only.
  --ro-bind-try silently skips missing paths for cross-distro
  safety.
- --proc /proc + --dev /dev + --tmpfs /tmp for required
  mount points the worker needs.
- --ro-bind allowed_read_paths (read access).
- --bind allowed_write_paths (rw access).
- --ro-bind-try allowed_commands (so bwrap doesn't fail on a
  missing optional binary).
- --unshare-net unless allow_network.
- Hygiene: --die-with-parent --new-session --unshare-pid.
- Worker tail: <python> -I -m forest_soul_forge.tools._sandbox_worker.

Error classification mirrors T1: setup_failed when bwrap is
missing OR user-namespaces unavailable; timeout for wall-clock
ceiling; sandbox-violation upgrade is the dispatcher's job
(T4 maps the worker's PermissionError to sandbox_violation
when the kernel-side EACCES surfaces).

default_sandbox() now returns LinuxBwrap on linux when bwrap
is in PATH, None otherwise. The None-return + permissive-mode
fallback chain remains operator-friendly: without bwrap, T7's
fallback kicks in.

Tests:
- TestLinuxBwrapArgvBuilder (7 cases) verifies argv shape
  invariants on every platform (pure string composition).
- TestLinuxBwrapSetupFailures (1) — patched-binary-missing
  smoke on every platform.
- TestLinuxBwrapIntegration (1, skipif-not-linux) — probes
  user-namespace availability via 'bwrap --bind / / true' and
  self-skips when userns is disabled on the host (hardened
  RHEL/CentOS).

Expected test count: 53 (T1) + 9 = 62 in the diag-b261
collection.

Out of scope for T2 (per ADR Decision):
- Dispatcher integration (T4 — does NOT touch dispatcher.py).
- Tool catalog YAML annotations for sandbox_eligible:false
  on memory_*/delegate/llm_think (T3).
- Audit chain event_data sandbox_* fields (T6).
- Permissive-mode fallback in dispatcher (T7).
- Runbook (T8)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 262 complete — ADR-0051 T2 (Linux bwrap) shipped ==="
echo "Press any key to close."
read -n 1
