#!/bin/bash
# Burst 129 — ADR-0044 P3: True headless mode + SoulUX clarification.
#
# Per the dependency audit, the kernel is mostly headless-clean
# already: zero FastAPI routes, lifespan steps, tests, or Python
# imports cross the kernel/userspace boundary. The remaining work
# is doc cleanup + an operator runbook + a smoke script that
# external integrators can use to verify the boundary holds.
#
# Originally budgeted as 3-5 bursts in the ADR-0044 roadmap.
# Reality: one burst because the surgery turned out to be
# semantic, not structural — the daemon already runs without
# frontend; it just wasn't documented as a supported path.
#
# What ships:
#
#   docs/runbooks/headless-install.md (new) — operator runbook
#     for the kernel-only install path. Three install paths:
#     pip install from source, Docker (`docker compose up daemon`),
#     PyInstaller daemon binary. Includes sanity-check curl
#     sequence, CORS tightening, auth token guidance, and an
#     explicit list of what's NOT in the kernel package.
#
#   scripts/headless-smoke.sh (new) — 6-stage curl-only validation
#     of the kernel API. Hits /healthz, /genres, /tools, /traits,
#     /skills, /plugins, /agents, /audit/tail. Asserts:
#       - 53+ tools registered
#       - 13+ genres registered
#       - 42+ roles registered post-Burst-124
#     If it passes against `python -m forest_soul_forge.daemon`
#     (no SoulUX scripts), the headless install holds. Future
#     P4 conformance test suite will build on this starting point.
#
#   src/forest_soul_forge/daemon/__main__.py — kernel-first
#     docstring + help text. Was framed in terms of two SoulUX
#     callers (Tauri shell, PyInstaller); now leads with "this is
#     the kernel's entry point — anyone can pip install +
#     python -m and have a running kernel," then notes the
#     SoulUX-specific invocations as examples. --port help text
#     no longer says "matches frontend's API expectation"; instead
#     says "the SoulUX reference frontend expects this port, but
#     any consumer can override."
#
#   src/forest_soul_forge/daemon/routers/audit.py — fix stale
#     docstring. Said "Source of truth is the canonical JSONL at
#     `data/audit_chain.jsonl`"; reality is examples/audit_chain.jsonl
#     is the configured default per daemon/config.py and CLAUDE.md.
#     Now references the configured path + FSF_AUDIT_CHAIN_PATH
#     override.
#
#   src/forest_soul_forge/daemon/config.py — CORS comment
#     clarifies it's a SoulUX-distribution default. Headless
#     consumers override to `[]` via FSF_CORS_ALLOW_ORIGINS=""
#     (or restrict to their own consumer's origin). Default
#     value unchanged — non-breaking for existing operators.
#
#   docker-compose.yml — header comments document the
#     SoulUX-vs-headless invocation distinction. `docker compose up`
#     (no args, default profile) brings up daemon + frontend
#     (SoulUX flagship). `docker compose up daemon` brings up
#     ONLY the kernel (the canonical headless invocation; works
#     today because docker compose accepts service names directly
#     and frontend's depends_on points at daemon, not the
#     other way around).
#
#   docs/architecture/kernel-userspace-boundary.md — clarifies
#     the `examples/` row. Was classified as pure userspace, but
#     `examples/audit_chain.jsonl` is the default audit_chain_path
#     per daemon/config.py (kernel-adjacent seed state) and
#     `examples/skills/*` are the canonical authored skill
#     manifests the install scripts copy. Now classified as
#     "hybrid" with the two exceptions called out explicitly.
#
#   README.md — Quick start section restructured into two
#     explicit paths: Path A SoulUX flagship (recommended for
#     first-time use; same behavior as before) and Path B
#     Headless kernel (pip install or `docker compose up daemon`,
#     pointers to the runbook + spec + smoke script).
#
#   STATE.md — frontend modules row prefixed "SoulUX-distribution
#     metric, not kernel" with a pointer to the headless runbook.
#     Numerical claim unchanged (22 modules); just framing.
#
# What this delivers per ADR-0044 P3:
#
#   ✅ True headless mode — documented operator path; verified by
#     the smoke script (which can be run against any Forest-kernel
#     build, not just this one).
#   ✅ SoulUX frontend split — the frontend is now explicitly a
#     SoulUX-distribution artifact in README, STATE, boundary doc,
#     docker-compose, and kernel docstrings. The kernel still
#     bundles the SoulUX defaults for operator convenience (CORS,
#     port 5173 expectation in --help) but every reference is
#     now framed as "the SoulUX flavor's default" rather than
#     "the kernel's default."
#
# What this does NOT do (and why):
#
#   - Does NOT change the audit_chain_path default away from
#     examples/audit_chain.jsonl. CLAUDE.md documents this as the
#     established convention; flipping it would break every
#     existing operator install. Boundary doc clarification
#     resolves the doctrinal contradiction without operator
#     migration cost.
#   - Does NOT break the docker-compose default. Plain
#     `docker compose up` continues to bring up daemon + frontend
#     (SoulUX behavior). Kernel-only is opt-in via
#     `docker compose up daemon`.
#   - Does NOT split into a separate kernel-only Python package.
#     The boundary is honored conceptually; physical package
#     separation can land later if the v0.7+ work warrants it.
#     Premature package split would force a name change
#     (forest-soul-forge-kernel?) that the v1.0 ABI freeze
#     would lock in for life.
#
# Verification:
#   - Full unit suite: 2,386 passing, 3 skipped (sandbox-only),
#     1 xfail (v6→v7 SQLite migration, pre-existing). Pure docs
#     + comment work; zero code-behavior touched.
#   - Audit chain hashes still link cleanly through entry 1121.
#   - scripts/headless-smoke.sh passes against the running daemon
#     (run `python -m forest_soul_forge.daemon` then the smoke
#     in another terminal).
#
# Closes ADR-0044 P3. Opens P4 (conformance test suite — the
# headless smoke is a starting point) and the path to P6 (first
# external integrator) is now clearer because the headless install
# is documented + verifiable.
#
# This is the first commit script that lands directly in
# dev-tools/commit-bursts/ per the Burst 128 convention. The
# commit-burst130*.command file in this directory is the next
# step in this arc.

set -euo pipefail

# We're at dev-tools/commit-bursts/, cd up to repo root.
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/runbooks/headless-install.md \
        scripts/headless-smoke.sh \
        src/forest_soul_forge/daemon/__main__.py \
        src/forest_soul_forge/daemon/routers/audit.py \
        src/forest_soul_forge/daemon/config.py \
        docker-compose.yml \
        docs/architecture/kernel-userspace-boundary.md \
        README.md \
        STATE.md \
        dev-tools/commit-bursts/commit-burst129-headless-mode.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(kernel): true headless mode + SoulUX clarification (ADR-0044 P3, B129)

Burst 129. Closes ADR-0044 P3 ('True headless mode + SoulUX
frontend split'). Originally budgeted at 3-5 bursts; reality was
one burst because the dependency audit found zero FastAPI routes,
lifespan steps, tests, or Python imports cross the kernel/
userspace boundary. The remaining work was doc cleanup + an
operator runbook + a verification smoke script.

Ships:

- docs/runbooks/headless-install.md (new): operator runbook for
  the kernel-only install path (pip install + python -m daemon,
  Docker via 'docker compose up daemon', PyInstaller binary).

- scripts/headless-smoke.sh (new): 6-stage curl-only validation
  of the kernel API. External integrators run this against any
  Forest-kernel build to verify ABI compatibility. Future P4
  conformance test suite builds on this.

- src/forest_soul_forge/daemon/__main__.py: kernel-first docstring
  + --port help text. Was SoulUX-flavored; now leads with 'this
  is the kernel's entry point' and notes SoulUX callers as
  examples.

- src/forest_soul_forge/daemon/routers/audit.py: fix stale
  docstring. Said 'data/audit_chain.jsonl'; configured default is
  examples/audit_chain.jsonl per daemon/config.py + CLAUDE.md.

- src/forest_soul_forge/daemon/config.py: CORS comment clarifies
  it's a SoulUX default; headless consumers override via
  FSF_CORS_ALLOW_ORIGINS. Default value unchanged.

- docker-compose.yml: header comments document
  'docker compose up' (SoulUX flagship default) vs.
  'docker compose up daemon' (canonical headless invocation —
  works today because frontend's depends_on points at daemon,
  not the reverse). Default behavior unchanged.

- docs/architecture/kernel-userspace-boundary.md: examples/ row
  reclassified 'hybrid'. examples/audit_chain.jsonl + examples/
  skills/* are kernel-adjacent seed state; the rest (plugins,
  scenarios, README content) is userspace.

- README.md: Quick start restructured into Path A (SoulUX
  flagship, recommended) + Path B (headless kernel, for
  integrators / second distributions / CI). Path A behavior
  identical to today.

- STATE.md: frontend modules row prefixed 'SoulUX-distribution
  metric, not kernel' with runbook pointer. Numerical claim
  unchanged.

What this does NOT do:
- Does NOT change audit_chain_path default. CLAUDE.md established
  examples/audit_chain.jsonl as canonical; flipping would break
  every existing operator install. Boundary doc clarification
  resolves the doctrinal mismatch without migration cost.
- Does NOT break docker-compose default. Plain 'docker compose
  up' continues to bring up daemon + frontend.
- Does NOT split into a separate kernel-only Python package.
  Premature; would force a name change the v1.0 ABI freeze
  would lock in.

Verification:
- Full unit suite: 2,386 passing (3 skipped, 1 xfail, all
  pre-existing). Pure docs + comment work; zero code-behavior
  touched.
- Audit chain hashes link cleanly through entry 1121.
- headless-smoke.sh passes against 'python -m forest_soul_forge.
  daemon' running in another terminal.

Closes ADR-0044 P3. Opens P4 (conformance test suite — headless
smoke is the starting point) and clears the path toward P6 (first
external integrator validation) by making the headless install
documented + verifiable.

First commit script landing directly in dev-tools/commit-bursts/
per the Burst 128 convention."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 129 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
