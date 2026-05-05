# Forest kernel API conformance test suite

ADR-0044 Phase 4. Operationalizes the contract specified in
[`docs/spec/kernel-api-v0.6.md`](../../docs/spec/kernel-api-v0.6.md).

This suite is **HTTP-only**. Tests do not import from
`forest_soul_forge.*`; they hit a running daemon at a configurable
URL and assert the documented contracts hold. That means an
external integrator can run this suite against ANY build of the
Forest kernel — including a non-Python implementation, a
PyInstaller binary, a different distribution's daemon — and get
a pass/fail report keyed to the spec's section numbers.

## When to use this

- **External integrators** verifying that their build of Forest
  honors the kernel API. If the conformance suite passes, your
  build is API-compatible with the spec at the version tested.
- **The Forest project itself** as a regression gate. Run before
  every release to confirm no breaking change slipped in.
- **Anyone debugging a Forest deployment** — these tests exercise
  the contract end-to-end and fail loudly if something's off.

## Install + run

```bash
# Install the suite's dependencies (separate from the daemon's deps):
pip install "forest-soul-forge[conformance]"

# Bring up a daemon to test against. Headless install per
# docs/runbooks/headless-install.md:
python -m forest_soul_forge.daemon &  # backgrounds the daemon
DAEMON_PID=$!

# Wait for it to be ready (~5 seconds on a cold start):
until curl -fsS http://127.0.0.1:7423/healthz > /dev/null; do sleep 1; done

# Run the conformance suite:
pytest tests/conformance/ -v

# Tear down:
kill $DAEMON_PID
```

By default the suite hits `http://127.0.0.1:7423`. Override with:

```bash
FSF_DAEMON_URL=https://my-forest-build.example.com pytest tests/conformance/ -v
```

## What it tests

One test file per spec section. Each test docstring cites the
exact spec section it enforces.

| File | Spec section | Surface |
|---|---|---|
| `test_section1_tool_dispatch.py` | §1 | Tool dispatch protocol — outcome shapes, governance pipeline observable behavior, mcp_call.v1 contract |
| `test_section2_audit_chain.py` | §2 | Audit chain — JSONL shape, hash-chain integrity, append-only, event type catalog |
| `test_section3_plugin_manifest.py` | §3 | Plugin manifest schema v1 — Pydantic validation, sha256 trust boundary |
| `test_section4_constitution.py` | §4 | Constitution.yaml schema — top-level fields, hash invariant |
| `test_section5_http_api.py` | §5 | HTTP API contract — endpoint catalog, auth model, idempotency, error envelope |
| `test_section6_cli.py` | §6 | CLI surface — subcommand tree, exit codes, auth fallback |
| `test_section7_schema.py` | §7 | Schema migrations — strict-additive policy, current version v15 |

## What it does NOT test

The suite is **black-box** — it tests what the documented contract
promises, not internal implementation details:

- Module layout, helper functions, private dataclass shapes
- Performance characteristics
- The reference frontend (`frontend/`), Tauri shell
  (`apps/desktop/`), or any other userspace artifact

If your build of Forest passes this suite, you have an
API-compatible kernel. Implementation choices below the API
surface are yours.

## Pass/fail report format

Default pytest output is sufficient. For external integrator
report-back, run with `--junitxml=conformance-report.xml` and
share that. For human-readable summary:

```bash
pytest tests/conformance/ -v --tb=short \
  | tee conformance-results.txt
```

A future P6 milestone may add a structured report generator
(`pytest --conformance-report-md` flag) that produces a
spec-section-keyed markdown table; not yet shipped.

## Versioning

This suite tests against **kernel API spec v0.6**. When the spec
bumps to v1.0 (post-ADR-0044 P6 external integrator validation),
this directory will fork:
- `tests/conformance/v0.6/` keeps the v0.6 tests for backward
  compatibility verification.
- `tests/conformance/v1/` lands the v1 tests.

A build can pass v0.6 conformance and not v1, or vice versa, or
both. The pass-set defines API-compatibility scope.

## References

- [`docs/spec/kernel-api-v0.6.md`](../../docs/spec/kernel-api-v0.6.md) — the spec these tests enforce
- [`KERNEL.md`](../../KERNEL.md) — the seven ABI surfaces overview
- [`docs/runbooks/headless-install.md`](../../docs/runbooks/headless-install.md) — how to bring up a daemon to test against
- ADR-0044 — the v0.6 kernel arc parent ADR (Phase 4 = this suite)
- `scripts/headless-smoke.sh` — the curl-only smoke that this suite extends with structured assertions
