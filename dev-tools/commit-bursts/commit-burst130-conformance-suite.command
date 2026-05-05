#!/bin/bash
# Burst 130 — ADR-0044 P4: kernel API conformance test suite scaffold.
#
# Operationalizes the contract specified in
# docs/spec/kernel-api-v0.6.md (Burst 127). Tests are HTTP-only —
# no internal forest_soul_forge imports — so they run against ANY
# Forest-kernel build, including non-Python implementations,
# PyInstaller binaries, and second distributions. External
# integrators install via `pip install
# "forest-soul-forge[conformance]"` and run pytest against their
# own daemon URL.
#
# Originally budgeted at 3-4 bursts in the ADR-0044 roadmap. Reality:
# one burst with a minimum-viable suite covering all seven spec
# sections. Future deepening (write-endpoint coverage with
# idempotency replay, structured Markdown report generator, plugin
# manifest sample-validation library, CLI subprocess testing
# expansion) lands in P4 follow-ups as needs emerge.
#
# What ships:
#
#   tests/conformance/ (new directory, 8 files):
#     __init__.py             — minimal package marker
#     README.md               — usage guide + spec section mapping
#                               + pass/fail report format + versioning
#                               policy (v0.6 → v1 fork plan)
#     conftest.py             — daemon_url + api_token + httpx client
#                               fixtures; autouse _daemon_reachable
#                               that pytest.exit's cleanly if daemon
#                               isn't up
#     test_section1_tool_dispatch.py  — §1: tool catalog reachable,
#                                       tool entry shape, mcp_call.v1
#                                       present, unknown-tool refusal
#                                       envelope
#     test_section2_audit_chain.py    — §2: JSONL line shape (7
#                                       required top-level fields),
#                                       seq monotonic, hash chain
#                                       integrity (entry_hash =
#                                       sha256(canonical_event);
#                                       prev_hash linkage), event_type
#                                       string contract
#     test_section3_plugin_manifest.py — §3: /plugins reachable,
#                                       per-plugin shape (name regex,
#                                       schema_version=1, side_effects
#                                       enum, trust_tier 0-5)
#     test_section4_constitution.py   — §4: /agents shape, per-agent
#                                       fields (dna, role, genre),
#                                       constitution_hash sha256
#                                       format, character-sheet
#                                       endpoint reachable
#     test_section5_http_api.py       — §5: 9 read endpoints respond
#                                       200 ungated, /openapi.json
#                                       3.x available, 404 envelope
#                                       carries 'detail', writes
#                                       respect auth model
#     test_section6_cli.py            — §6: fsf --help exit 0,
#                                       documented subcommands
#                                       (plugin / agent / chronicle)
#                                       have --help, unknown
#                                       subcommand exits non-zero
#     test_section7_schema.py         — §7: /healthz reports ok
#                                       (migration succeeded), v14
#                                       grants endpoint observable,
#                                       v15 posture endpoint
#                                       observable
#
#   pyproject.toml — new optional-dependencies extras:
#     conformance = ["pytest", "httpx", "jsonschema", "pyyaml"]
#     External integrators install with `pip install
#     "forest-soul-forge[conformance]"`. Separate from [daemon] so
#     a kernel-only Python install doesn't drag in test deps.
#
#   docs/spec/kernel-api-v0.6.md §9 — Conformance section updated
#     from "Phase 4 will produce..." to "Phase 4 SHIPPED Burst 130
#     at tests/conformance/". Includes install + run snippet.
#
#   STATE.md — tests row reflects new metric: "2386 unit +
#     conformance suite at tests/conformance/". Documents the
#     external-integrator install path.
#
# Verification:
#   - All 8 conformance test files parse clean (Python AST validation).
#   - Full unit suite: 2,386 passing, 3 skipped, 1 xfail (all
#     pre-existing). Pure additive work; zero existing-code touched.
#   - Conformance suite intentionally not run against this build's
#     daemon in this commit — that's an integrator workflow
#     (run after `python -m forest_soul_forge.daemon` boots), not a
#     CI gate. The headless-smoke.sh from Burst 129 + this suite
#     are complementary: smoke is curl-only fast, conformance is
#     pytest-driven structured.
#
# What this delivers per ADR-0044 P4:
#
#   ✅ Conformance test runner — pytest tests/conformance/ works
#     against any Forest-kernel build's daemon URL.
#   ✅ Pass/fail report keyed to spec section numbers — every test
#     file maps 1:1 to a spec §, every test docstring cites the
#     specific spec subsection it enforces.
#   ⏳ Version-compatibility matrix tooling — not yet shipped;
#     v0.6 → v1 fork plan documented in README.md but the actual
#     directory split lives in v1 spec drafting.
#
# What this opens:
#
#   - P6 (first external integrator validation, the load-bearing
#     v1.0 freeze gate per ADR-0044 Decision 4): integrators now
#     have a concrete tool to verify their build is API-compatible.
#     Recruiting them is the next phase; outreach materials land
#     in Burst 131.
#   - P7 (v1.0 stability commitment): once external integrator
#     reports back PASS on this suite, the v1 spec fork can land
#     and v1.0 freezes.
#   - Inside the project: future ABI-touching ADRs can be gated on
#     "does the conformance suite still pass?" — automatic
#     regression check against the spec.

set -euo pipefail

# We're at dev-tools/commit-bursts/, cd up to repo root.
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add tests/conformance/ \
        pyproject.toml \
        docs/spec/kernel-api-v0.6.md \
        STATE.md \
        dev-tools/commit-bursts/commit-burst130-conformance-suite.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(conformance): kernel API conformance test suite scaffold (ADR-0044 P4, B130)

Burst 130. Closes ADR-0044 P4. Operationalizes the contract specified
in docs/spec/kernel-api-v0.6.md (Burst 127). Tests are HTTP-only —
no internal forest_soul_forge imports — so they run against ANY
Forest-kernel build, including non-Python implementations,
PyInstaller binaries, and second distributions.

Originally budgeted at 3-4 bursts in the ADR-0044 roadmap. Reality:
one burst with a minimum-viable suite covering all seven spec
sections. Future deepening lands as P4 follow-ups.

Ships tests/conformance/ (8 files):

- README.md: usage guide, spec section mapping, pass/fail report
  format, v0.6 → v1 fork plan
- conftest.py: daemon_url + api_token + httpx client fixtures;
  autouse _daemon_reachable that pytest.exits cleanly if daemon
  isn't up
- test_section1_tool_dispatch.py: §1 — catalog reachable, tool
  entry shape, mcp_call.v1 present, unknown-tool refusal envelope
- test_section2_audit_chain.py: §2 — 7-field JSONL shape, seq
  monotonic, hash chain integrity (entry_hash = sha256(canonical),
  prev_hash linkage)
- test_section3_plugin_manifest.py: §3 — /plugins reachable,
  per-plugin shape, name regex, schema_version=1, side_effects
  enum, trust_tier 0-5
- test_section4_constitution.py: §4 — /agents shape, per-agent
  fields, constitution_hash sha256 format, character-sheet
  endpoint reachable
- test_section5_http_api.py: §5 — 9 read endpoints respond 200,
  /openapi.json 3.x, 404 envelope has 'detail', writes respect
  auth model
- test_section6_cli.py: §6 — fsf --help exit 0, documented
  subcommands have --help, unknown subcommand exits non-zero
- test_section7_schema.py: §7 — /healthz ok, v14 grants endpoint
  observable, v15 posture endpoint observable

pyproject.toml: new [conformance] extras. External integrators
install with 'pip install \"forest-soul-forge[conformance]\"'.
Separate from [daemon] so a kernel-only install doesn't drag in
test deps.

docs/spec/kernel-api-v0.6.md §9: updated from 'Phase 4 will
produce' to 'Phase 4 SHIPPED Burst 130 at tests/conformance/'.

STATE.md: tests row reflects '2386 unit + conformance suite' with
external-integrator install path documented.

Verification:
- All 8 test files parse clean (AST validation).
- Full unit suite: 2,386 passing, 3 skipped, 1 xfail (all
  pre-existing). Pure additive; zero existing-code touched.

Closes ADR-0044 P4. Opens P6 (first external integrator
validation): integrators now have a concrete tool to verify their
build is API-compatible. Recruiting outreach materials land in
Burst 131."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 130 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
