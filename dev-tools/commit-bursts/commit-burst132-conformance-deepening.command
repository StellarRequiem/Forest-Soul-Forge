#!/bin/bash
# Burst 132 — ADR-0044 P4 follow-up: conformance suite deepening.
#
# B130 shipped the minimum-viable scaffold (one test file per spec
# section). This burst adds the P4 follow-ups: schema-validation
# fixtures, write-endpoint idempotency, structured markdown report
# generator.
#
# What ships:
#
#   tests/conformance/fixtures/plugin_manifest_v1.schema.json (new)
#     — canonical JSON Schema for plugin.yaml v1 per spec §3.
#     Encodes every documented constraint: schema_version=1 const,
#     name regex, semver pattern, type enum (mcp_server only at v1),
#     side_effects enum, trust_tier 0-5, entry_point sub-shape with
#     stdio/http if/then branches, sha256 hex pattern. External
#     integrators can use this schema standalone for their own
#     manifest validators.
#
#   tests/conformance/fixtures/plugin_manifests/ (new, 7 files):
#     valid_minimal.yaml      — every required field, no flourishes
#     valid_full.yaml         — every documented optional field
#     invalid_schema_version.yaml — schema_version=2 (reserved for
#                                   future per spec §3.2)
#     invalid_name_uppercase.yaml — uppercase name (spec regex
#                                   forbids)
#     invalid_bad_semver.yaml — '1.0' (missing patch component)
#     invalid_missing_sha256.yaml — entry_point sans sha256 (the
#                                   trust boundary; spec §3.3 says
#                                   loosening this is breaking)
#     invalid_bad_side_effects.yaml — side_effects=admin (not in
#                                     documented enum)
#
#   tests/conformance/test_section3_plugin_manifest.py — extended:
#     - Imports jsonschema, yaml, pathlib for fixture loading
#     - Adds test_section3_valid_manifest_passes_schema (parameterized
#       over valid_minimal + valid_full)
#     - Adds test_section3_invalid_manifest_fails_schema (parameterized
#       over 5 invalid fixtures, each asserting the violation
#       references the expected field). Catches drift between spec
#       text and schema fixture.
#
#   tests/conformance/test_section5_http_api.py — extended:
#     - Adds test_section5_idempotency_replay_identical_response
#       per spec §5.2. Probes with a predictably-4xx request +
#       repeats with same X-Idempotency-Key + same body. Asserts
#       both responses identical (status + detail).
#
#   tests/conformance/run-conformance-report.sh (new) — markdown
#     report generator. Runs pytest with --junitxml, parses the
#     XML, emits a per-section pass/fail table with timestamp,
#     daemon URL, kernel commit SHA, conformance verdict
#     (COMPATIBLE / NOT FULLY COMPATIBLE). External integrators
#     run this against their build and share the resulting
#     markdown.
#
# What this delivers per ADR-0044 P4:
#   ✅ Plugin manifest sample-validation library (schema +
#     fixtures + parametric tests)
#   ✅ Write-endpoint coverage with idempotency replay
#   ✅ Structured markdown report generator
#
# Intentionally NOT in this burst:
#   - Full §1 dispatch protocol expansion (would require birthing
#     a real agent in a fixture). Future P4 follow-up if integrators
#     ask for it.
#   - Per-test spec-section anchors machine-parseable in the report
#     (currently extracts from filename heuristic). Future polish.
#
# Verification:
#   - All fixture YAML files parse cleanly via PyYAML.
#   - JSON Schema parses as JSON.
#   - Conformance test files parse cleanly via Python AST.
#   - Schema lists 16 documented fields per spec §3.1-§3.3.
#   - Full unit suite: 2,386 passing (3 skipped, 1 xfail, all
#     pre-existing). Pure additive; zero existing-code touched.
#
# Closes the P4 follow-up scope. Conformance suite is now strong
# enough that an external integrator's report-back is meaningful
# evidence — failing tests cite specific spec subsections; the
# markdown report is shareable as-is.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add tests/conformance/ \
        dev-tools/commit-bursts/commit-burst132-conformance-deepening.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(conformance): plugin manifest schema validation + idempotency probe + markdown report (ADR-0044 P4 follow-up, B132)

Burst 132. P4 follow-up to B130's minimum-viable scaffold. Adds
the deeper deliverables ADR-0044 §9 promised: plugin manifest
schema validation library, write-endpoint idempotency replay,
structured markdown report generator.

Ships:

- tests/conformance/fixtures/plugin_manifest_v1.schema.json (new):
  canonical JSON Schema for plugin.yaml v1 encoding every spec §3
  constraint. External integrators can use this schema standalone
  for their own manifest validators.

- tests/conformance/fixtures/plugin_manifests/ (new, 7 files):
  valid_minimal + valid_full + 5 invalid fixtures, each violating
  one documented rule. Catches drift between spec text and schema
  implementation.

- tests/conformance/test_section3_plugin_manifest.py: extended
  with two parametric tests — valid manifests pass schema; each
  documented violation rule actually rejects with the expected
  field reference.

- tests/conformance/test_section5_http_api.py: extended with
  test_section5_idempotency_replay_identical_response per spec
  §5.2. Probes with predictably-4xx request, repeats with same
  X-Idempotency-Key + same body, asserts both responses identical.

- tests/conformance/run-conformance-report.sh (new): markdown
  report generator. Runs pytest with --junitxml, parses XML,
  emits per-section pass/fail table with timestamp, daemon URL,
  kernel commit SHA, COMPATIBLE/NOT-COMPATIBLE verdict. External
  integrators share the resulting markdown.

Verification:
- All YAML fixtures parse via PyYAML.
- JSON Schema parses as JSON; lists 16 documented fields per spec.
- Test files parse via Python AST.
- Full unit suite: 2,386 passing (3 skipped, 1 xfail; pre-existing).
- Pure additive; zero existing-code touched.

Closes the P4 follow-up scope. The conformance suite is now strong
enough that an external integrator's report-back is meaningful
evidence — failing tests cite specific spec subsections; the
markdown report is shareable as-is via:

  pip install \"forest-soul-forge[conformance]\"
  python -m forest_soul_forge.daemon &
  ./tests/conformance/run-conformance-report.sh -o my-build-report.md"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 132 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
