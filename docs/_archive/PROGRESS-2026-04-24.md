# Progress

As of **2026-04-24**.

This doc tracks current state between commits. Higher-fidelity history lives in `CHANGELOG.md` (what landed in each tagged change) and `docs/decisions/` (why each architectural decision was made).

## Shipped — committed to `main`

The last two commits (`1fa69ba` initial, `0a9c9df` Phase 2 remainder) established:

- **Trait tree v0.1** — 5 domains, 26 traits (`config/trait_tree.yaml`).
- **Trait engine** — YAML load, schema validation, role-weighted domain scoring, flagged-combination scanner (`src/forest_soul_forge/core/trait_engine.py`).
- **Soul generator** — deterministic natural-language persona from a `TraitProfile` (`src/forest_soul_forge/soul/generator.py`).
- **Agent DNA + lineage** — SHA-256 of canonical trait payload, 12-char short + 64-char full, parent / ancestry tracking (`src/forest_soul_forge/core/dna.py`, `lineage.py`).
- **Grading engine** — role-weighted config grade, per-domain breakdown, deterministic dominant-domain selection (`src/forest_soul_forge/core/grading.py`).
- **Constitution builder** — three-layer prompt assembly with strictness-wins precedence and content-addressed `constitution_hash` (`src/forest_soul_forge/core/constitution.py`).
- **Audit chain** — append-only JSONL, SHA-256 linked, tamper-evident under operator-honest-but-forgetful threat model (`src/forest_soul_forge/core/audit_chain.py`).
- **Demo** — `scripts/demo_generate_soul.py` wires the whole Phase 2 stack end-to-end.
- **11 worked examples** — 5 role defaults, 2 stress cases, 3-generation lineage, 1 sample audit chain.
- **ADRs 0001–0005** — trait tree structure, DNA canonicalization, grading engine, constitution builder, audit chain. See `docs/decisions/`.
- **Original vision handoff** — `docs/vision/handoff-v0.1.md`.

## In working tree — not yet committed

These are the Phase 3 bootstrap changes. They will land together as the next commit once Phase 3 proper is further along.

- **Trait tree v0.2** — adds `embodiment` domain with `presentation` subdomain (three tertiary traits: `visual_density`, `signature_warmth`, `motion_liveliness`). Totals become 6 domains / 29 traits. Backward-compatible superset; all existing traits unchanged.
- **Role weights for embodiment** — added to all 5 roles. Investigative roles (`network_watcher`, `log_analyst`) de-emphasize (0.5); user-facing roles (`incident_communicator`, `operator_companion`) emphasize (1.2 and 1.5).
- **Grading tie-break fix** — `CANONICAL_DOMAIN_ORDER` extended with `embodiment`; introduced `TIE_EPSILON = 1e-9` to absorb floating-point drift from tertiary tier weight (0.3 is not exactly representable in binary, and all-tertiary subdomains accumulated rounding that broke bare `==` tie comparison). Latent correctness fix, predates v0.2 but was masked by the trait mix.
- **Regenerated examples** — all 11 souls rerun under v0.2. Every DNA shifted, as expected (adding traits changes the canonical trait payload).
- **ADR-0006 Registry as index** — SQLite over canonical artifacts, one-way sync, `rebuild_registry()` as escape hatch.
- **ADR-0007 FastAPI daemon** — localhost:7423 by default, asyncio.Lock serialized writes, WAL-mode concurrent reads, optional `FSF_API_TOKEN` auth, `/preview` zero-write slider-feedback path. (App path corrected to `forest_soul_forge.daemon.app:app`.)
- **ADR-0008 Local-first model provider** — `LocalProvider` is the default, `FrontierProvider` is opt-in, `TaskKind` routing enables multi-model-per-task. Frontend "switch provider" button hits `PUT /runtime/provider`.
- **SQLite registry module + tests** — `src/forest_soul_forge/registry/` with `schema.py`, `ingest.py`, `registry.py`, `__init__.py`. 10-agent `examples/` rebuild passes end-to-end with deterministic legacy instance IDs. Lineage resolution handles shared-DNA siblings via `spawned_by` disambiguation (fix applied 2026-04-24).
- **FastAPI daemon skeleton (read-only)** — `src/forest_soul_forge/daemon/` with `app.py`, `config.py`, `deps.py`, `schemas.py`, `providers/{base,local,frontier,__init__}.py`, `routers/{health,agents,audit,runtime}.py`. Endpoints: `GET /healthz`, `GET /agents[?role=&status=]`, `GET /agents/{id}`, `GET /agents/{id}/ancestors`, `GET /agents/{id}/descendants`, `GET /agents/by-dna/{dna}`, `GET /audit/tail?n=N`, `GET /audit/agent/{id}`, `GET /audit/by-dna/{dna}`, `GET /runtime/provider`, `PUT /runtime/provider`. Requires the new `[daemon]` pyproject extra.
- **Provider registry tests** — 7 stdlib-only tests covering default-is-local, flip, reset, unknown-rejection, `TaskKind` string stability.
- **Daemon integration tests** — TestClient-based, skipped gracefully in sandbox when FastAPI isn't installed; cover all read endpoints + provider switch.
- **Write endpoints (`/birth`, `/spawn`, `/archive`)** — shipped to working tree 2026-04-24. Registry schema v2 with `sibling_index` column + composite index; `SoulGenerator` + `ParsedSoul` + ingest extended to round-trip `instance_id` / `parent_instance` / `sibling_index`; `agent_archived` added to known event types; `DaemonSettings` gained `trait_tree_path` / `constitution_templates_path` / `soul_output_dir` / `allow_write_endpoints`; app lifespan bootstraps `trait_engine` / `audit_chain` / `threading.Lock` write_lock on app.state. Ordering is artifact → chain → registry per ADR-0006, with artifact rollback on chain-append failure. Constitution override is optional and SHA-256-folded into the constitution hash (Path D). Writes-disabled flag blocks all three endpoints at 403 via a dep. **14/14 write tests green on Mac** (`tests/unit/test_daemon_writes.py`): birth/spawn/archive happy paths, error paths (400/404/403), twin sibling_index=2, override changes hash, child lineage, idempotent archive, audit mirror for both `agent_created` and `agent_archived`. Caught one bug during verification: archive wasn't mirroring its chain entry into the registry's `audit_events` table, so `/audit/tail` didn't see it — fixed with an explicit `register_audit_event` call inside the write_lock.
- **`X-Idempotency-Key` layer** — shipped to working tree 2026-04-24. New `idempotency_keys` table (added via `CREATE IF NOT EXISTS`, no schema version bump), `Registry.lookup_idempotency_key` / `store_idempotency_key`, `IdempotencyMismatchError` → 409. Request hash is `sha256(endpoint || NUL || json.dumps(body, sort_keys=True, default=str))`; lookup happens *inside* the write_lock so same-key same-body concurrent requests can't both execute. Cached replay returns a raw `fastapi.Response` with the original bytes + status, bypassing `response_model` re-serialization so clients get the same bytes back. Archive's already-archived short-circuit is deliberately not cached (derived read, not a mutation). 5/5 `TestIdempotency` cases green.
- **`X-FSF-Token` auth** — shipped to working tree 2026-04-24. `DaemonSettings.api_token: SecretStr | None` (env `FSF_API_TOKEN`). When set, all non-`/healthz` routes require the exact token or return 401. `/healthz` stays unauthenticated and surfaces `auth_required` so a UI can decide whether to prompt. 4/4 `TestAuth` cases green.
- **`GET /traits`** — shipped to working tree 2026-04-24. Read endpoint serving the full loaded trait tree as JSON (domains → subdomains → traits with scale + tier + description). Source of truth for the frontend's slider panel and radar chart. 1/1 test green.
- **`POST /preview`** — shipped to working tree 2026-04-24. Zero-write derivation path: builds soul markdown + constitution YAML + `constitution_hash` + `grade_report` + DNA for a given profile without touching disk, the chain, or the registry. Override folds into the hash identically to `/birth` so preview → birth produces bit-identical hashes. Powers slider-drag feedback without churning artifacts. 4/4 `TestPreviewEndpoint` cases green.
- **Docker test infrastructure** — shipped to working tree 2026-04-24. `Dockerfile.test` (Python 3.12-slim, deps pinned to `[dev]` + `[daemon]`), `.dockerignore`, `scripts/docker_test.sh` (bind-mounts `$PWD:/app`, auto-rebuilds on `Dockerfile.test` or `pyproject.toml` mtime change). Solves the "sandbox has no PyPI" problem — tests now run against the user's local Docker daemon rather than the constrained sandbox Python. Full suite **28/28 green** on Mac. Separate from the Task #8 production compose on purpose.
- **CHANGELOG entries** — Phase 3 bootstrap + write endpoints + idempotency/auth/preview/traits/Docker sections added.
- **Stdlib test harness** — `scripts/run_tests_no_pytest.py` now provides fresh `tmp_path` per test invocation AND supports `pytest.importorskip` so daemon-only test modules report as skipped rather than failing when FastAPI isn't present.
- **Demo script robustness** — truncate-in-place instead of unlink for `examples/audit_chain.jsonl` (sandbox mount blocks unlink on files it allows to write).
- **Frontend placeholder directory** — `frontend/` scaffolding, untracked, currently defaults to `claude` provider in `llm-client.js` — rewire pending.

## Near-term Phase 3 queue

In execution order:

1. ✅ **SQLite registry module** — shipped to working tree. Schema, ingest, rebuild, closure-table lineage, 164 passing unit tests. Not yet committed.
2. ✅ **FastAPI daemon skeleton (read-only)** — shipped to working tree. Routers for health / agents / audit / runtime, provider abstraction (local-first per ADR-0008), TestClient integration tests. Requires `[daemon]` extra. Not yet committed.
3. ✅ **Write endpoints** — `/birth`, `/spawn`, `/archive` shipped to working tree 2026-04-24. 14/14 tests green.
4. ✅ **Idempotency + auth + preview + traits** — shipped to working tree 2026-04-24. `X-Idempotency-Key` on all three writes (table + lookup/store + 409-on-mismatch + cached replay). `X-FSF-Token` via optional `FSF_API_TOKEN`. `GET /traits` and `POST /preview` (zero-write, Path-D hash parity with `/birth`). 28/28 tests green on Mac via Docker. Strict CORS allowlist still pending — will be tuned when the real frontend origin is known during step 5.
5. ✅ **Docker test infrastructure** — shipped to working tree 2026-04-24. `Dockerfile.test` + `.dockerignore` + `scripts/docker_test.sh` (deps-only image, source bind-mounted, auto-rebuild on mtime drift).
6. **Frontend rewire** — replace static mock data and the `window.LLM` placeholder with daemon calls; preserve the existing slider UX. Calls `GET /traits` for the slider panel, `POST /preview` for live slider feedback, `POST /v1/birth` / `/spawn` / `/archive` for mutations (generating `X-Idempotency-Key` per submit), provider-switch button calls `PUT /runtime/provider`, status dot reads from `GET /healthz`. Option B direction: vanilla JS rewrite, no build step, dead React/ESM scaffolding removed.
7. **Docker compose (production)** — daemon container + static frontend serve (prod shape); `uvicorn --reload` is the dev shape. Separate from the test image above.
8. **Phase 3 commit and push** — land trait tree v0.2, grading fix, regenerated examples, ADRs 0006–0008 + 0016, registry + daemon + idempotency + auth + traits + preview + frontend, Docker test infra + production compose, CHANGELOG, and this PROGRESS update together.

## Proposed ADR slate — not yet written

Captured from design discussions. All will be filed as `Proposed` status until implementation gets close; several depend on experience from real test cases to specify well.

- ✅ **ADR-0008 Local-first model provider** — shipped to working tree, covers the provider abstraction, `TaskKind` routing, Local/Frontier defaults. Not yet committed. (Previously slotted for the provenance-bundle decision; that moves to ADR-0009.)
- **ADR-0009 Provenance bundle** — a signed wrapper around `{agent_traits.json, constitution.yaml, soul.md, dna, parent_dna, born_at, minted_by}` that travels with the agent. "Birth certificate."
- **ADR-0010 Certification record** — post-training attainment record. An agent finishes its training-to-benchmark phase and receives a certificate documenting the capabilities, standards, and safety properties it met. Distinct from the birth certificate (which describes the configured agent) because it describes the validated agent.
- **ADR-0011 Continuity protocol** — what an agent does when it wakes up from unexpected downtime (power loss, host crash, long pause). Detect the gap, note curiosity / surprise appropriately in the audit chain as an `agent_resumed` event, re-check state rather than assume continuity. Also specifies the self-observation hooks — once provenance is established, the agent can observe and reason about its own chain.
- **ADR-0012 Tamper-proof provenance upgrade path** — not in the default tier. Reserved for VIP / high-risk clients who need deepfake-defense-grade integrity: hardware-backed signing, external timestamping, periodic external anchoring. Documented now so Phase 2's hash-linked JSONL doesn't close off the option.
- **ADR-0013 Central mint + local runtime hybrid** — consumer product architecture. Central forge creates the agent, installer delivers it, everything after delivery runs fully local on the client. Central registry retained for audit / investigation assistance only; no runtime dependency on it. Compatible with local-first because the runtime contract stays local.
- **ADR-0014 Accessibility-adaptive interaction layer** — the co-equal core purpose described in the top-level README. Covers: (a) baseline rapport-building behavior on every agent, (b) operator-declared accommodations, (c) host-OS accessibility signals, (d) the medical / therapeutic tier supporting real-time audio-video, consumer-or-custom peripherals, and guardian-provided profile data. Core agent purpose, not a feature flag.
- **ADR-0015 Baseline mental / emotional / physical status check** — specifies the standard every-agent behavior: what the agent checks for, cadence, signal sources (input patterns, explicit declaration, turn-taking cues), and response protocol. Split from ADR-0014 because the concern is different — 0014 is about stable accommodation for who the user is; 0015 is about moment-to-moment state.
- **ADR-0016 Session modes + self-spawning cipher** — the session is a first-class concept, not just "a conversation." Two modes: *ephemeral* (memory kept only for the session, flushed on close) and *persistent-fork* (session forks a dated branch of the agent's memory and merges back on close, with a diff the user can accept/reject). The *self-spawning cipher* is a deferred crypto primitive for the secure/therapeutic tier: at initial handshake the user and the agent jointly spawn a session-scoped keypair via a protocol neither party can forge alone, so even a compromised daemon can't impersonate past session state. Both memory-fork semantics and the cipher handshake need real-use experience before they lock — hence Proposed status now, Accepted only once a concrete caller exists.

## Vision / business doc — pending

`docs/business/vision.md` — licensing tiers (consumer, professional, enterprise, VIP), contract templates, product-line shape, Nevada foundry hardware aspiration. Captured here rather than in an ADR because it is a commercial / product document, not a per-decision architecture record.

## Known uncommitted correctness delta

The grading tie-break `TIE_EPSILON` change is a latent bug fix that predates v0.2. It got exposed because the new all-tertiary `presentation` subdomain accumulates enough 0.3 rounding to fail bare `==` tie comparison. When the Phase 3 commit lands, the CHANGELOG will note this is a fix, not a feature of v0.2.
