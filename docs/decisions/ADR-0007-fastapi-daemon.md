# ADR-0007 — FastAPI Daemon as Frontend Backend

- **Status:** Accepted
- **Date:** 2026-04-23
- **Supersedes:** —
- **Related:** ADR-0002 (DNA/lineage), ADR-0004 (constitution), ADR-0005 (audit chain), ADR-0006 (registry-as-index)

## Context

Phase 1-2 shipped the core engines as a pure Python library: trait tree, soul generator, grading, constitution builder, audit chain. Exercised via `scripts/demo_generate_soul.py` and sandbox verify scripts. No process boundary, no HTTP, no UI coupling.

Phase 3 adds a frontend. The browser shell at `frontend/index.html` currently has a hardcoded "Silent Warden-01" demo button and three dead `ForestApp-*.js` files left over from design-tab iteration. It does not call Python, does not know about trait tree, does not know about DNA or constitutions. Any real interaction (birth an agent, preview a soul, inspect lineage, replay audit events) needs a path from browser → Python engines → canonical artifacts → registry index.

Three shapes considered for that path:

1. **Pure in-browser.** Port the engines to JavaScript. Rejected: duplicates correctness-critical code (DNA hash, canonical JSON, grading math) across two languages. The Python versions are already tested and stable; any JS reimplementation ships with zero test parity and drifts on every refactor. Also puts soul.md / constitution.yaml generation in a sandbox with no filesystem access — artifacts would have to be downloaded, defeating the "local-first" property.
2. **Electron or similar desktop shell.** Bundles Python + Node + Chromium. Heavy. Phase 3 is not the time to take on an Electron build pipeline when a 200-line FastAPI app gets the same result with an order of magnitude less operational surface.
3. **Local HTTP daemon (FastAPI).** Python stays authoritative. Browser is a thin client. Daemon is the single process that touches canonical artifacts, appends to the audit chain, and writes to the registry.

Option 3 wins on cost, correctness coupling, and operational simplicity. This ADR pins the contract.

## Decision

A FastAPI daemon — single process, localhost-bound by default — is the **only** production path from frontend to engines. Direct frontend → filesystem and direct frontend → Python bridges (like the `window.claude.complete` placeholder in `llm-client.js`) are explicitly not production paths; they are scaffolding.

### Process shape

- One process. `uvicorn forest_soul_forge.daemon.app:app --host 127.0.0.1 --port 7423`. (Implemented under the ``daemon/`` package for self-descriptive naming; earlier drafts said ``api/``.)
- Localhost-bound by default. No LAN exposure unless the operator flips a config flag and accepts the consequence.
- Python 3.11+. FastAPI + uvicorn + pyyaml (already a dep) + python-multipart (if we take file uploads, defer until needed).
- Lifespan hook constructs and caches: the `TraitEngine`, the `AuditChain` handle, the `Registry` handle, the constitution templates loader. All three file-backed singletons live for the life of the process. The process is the thing that serializes writes.

### Concurrency model

- **Writes are serialized.** One `asyncio.Lock` in front of every write endpoint (`/birth`, `/spawn`, `/archive`, anything that touches the audit chain or filesystem). Audit chain appends are not safe under concurrent fsync; registry writes must see post-append audit state; artifact writes must not race with registry rebuilds. A single lock is the cheapest correct answer. If this ever becomes a bottleneck (it will not in v0.1) we revisit.
- **Reads are concurrent.** Registry reads go through SQLite with `PRAGMA journal_mode=WAL` so readers don't block writers; readers see a consistent snapshot per statement. Artifact reads (`/agents/{id}/soul`, `/agents/{id}/constitution`) are direct file reads — the canonical artifact is immutable once written (ADR-0005, ADR-0006) so stale reads are impossible.

### Auth

v0.1: **optional shared-secret header.** If `FSF_API_TOKEN` is set in the environment, the daemon requires `X-Forest-Token: <value>` on every request. If unset, no auth. This is local-first, blue-team-only, running on the operator's own box — a token is a seatbelt against "I left the daemon on and opened a drive-by-fetch in my browser," not a security boundary. Any multi-user scenario is out of scope for this ADR and would supersede it.

CORS: the daemon sets `Access-Control-Allow-Origin` to an explicit list — by default the origin(s) the frontend is served from (e.g. `http://127.0.0.1:5173` during dev, `http://127.0.0.1:7423` when served static by the daemon itself). No wildcard. No credentials mode.

### Endpoints (v0.1 surface)

Versioned at `/v1/`. Everything below is implicitly prefixed.

**Read:**

- `GET /roles` — list of role presets (pulled from `trait_tree.yaml`).
- `GET /traits` — full trait tree (domains, subdomains, traits, bands). The frontend needs this to render sliders without hardcoding.
- `GET /agents` — list of agents from the registry. Query params: `status`, `role`, `parent_dna`, `limit`, `offset`.
- `GET /agents/{dna_or_instance}` — full agent record: registry row + soul frontmatter + constitution path. Accepts either 12-char DNA or instance UUID.
- `GET /agents/{id}/soul` — raw soul.md bytes (`text/markdown`).
- `GET /agents/{id}/constitution` — constitution YAML (`text/yaml`).
- `GET /agents/{id}/lineage` — ancestor chain (closure-table walk from `agent_ancestry`).
- `GET /agents/{id}/descendants` — forward walk, same table.
- `GET /audit?since_seq=N&limit=M` — page through audit events (registry index; the JSONL remains canonical).
- `POST /preview` — body: `TraitProfile`-shaped JSON. Response: `{ dna, grade, constitution_hash, soul_markdown, warnings }`. **Writes nothing.** This is what powers live slider feedback in the UI. Bounded cost; no filesystem or chain mutation.

**Write:**

- `POST /birth` — body: profile + agent_name + optional role. Creates a root agent. Writes soul.md, writes constitution.yaml, appends `agent_created` to audit chain, inserts registry row. Response: the full agent record.
- `POST /spawn` — body: parent `dna_or_instance` + child profile + agent_name. Validates parent exists and is not archived, builds lineage via `Lineage.from_parent`, same write path as `/birth` but event type `agent_spawned` and `parent_dna` populated.
- `POST /archive/{id}` — mark an agent archived in the registry. Appends `agent_archived` event (new event type — ADR-0005 reserved `policy_violation_detected` etc.; this needs to be added to the enum). Artifacts stay on disk untouched; archive is a registry-only status flip.
- `POST /admin/rebuild-registry` — scan all artifacts + audit chain, rebuild SQLite from scratch (ADR-0006 escape hatch). Expensive; intentionally under `/admin`. Same auth rules.

**Intentionally out of scope for v0.1:**

- Editing a soul or constitution after creation. The canonical artifacts are immutable by design; a `/regenerate` endpoint that produces a new agent with a different DNA is a future ADR, not this one.
- Streaming endpoints. No SSE / WebSocket. `GET /audit` with polling is enough for the UI to feel live at the scale a single operator produces events.
- Multi-tenant `owner_id` filtering. Field exists in the registry (ADR-0006) but is unused in v0.1.

### Request/response invariants

- Every write endpoint accepts an **idempotency key** header `X-Idempotency-Key`. If a write with the same key has already succeeded, return the cached response; do not double-write. Keyed store in SQLite, 24-hour TTL. This is the answer to "the user double-clicked the Birth button and now there are two agents with different DNAs but the same name."
- Every error response is `{ "error": { "code": "<kebab>", "message": "<human>", "detail": { ... } } }`. No stack traces. No leaking filesystem paths that include the operator's home directory.
- `POST /preview` and `GET /traits` must respond in < 50ms on a warm cache. These fire on every slider change; anything slower and the UI feels broken.

### Failure modes we explicitly handle

1. **Partial write** — soul.md landed on disk, audit append failed. The audit chain is the source of truth for "which agents exist according to the operator's intent." Fix: write audit chain first, then artifact, then registry. If the audit append succeeds but the artifact write fails, the `rebuild_registry` will reconcile on next run — the operator sees a `missing_artifact` warning. If the artifact write succeeds but the audit append never happened, the artifact is orphaned and `rebuild_registry` surfaces it as an `orphan_artifact` warning. Both are recoverable.
2. **Audit chain corruption** — the daemon refuses all writes if `AuditChain.verify()` returns `ok=False` on startup or after a failed append. Reads still work (they come from the registry, which is an index, not the authoritative source). The operator runs the repair workflow (back up the chain, find the break, replay from a known-good seq) documented in `audit/README.md`.
3. **Registry drift** — the audit chain disagrees with the registry about whether agent X exists. On startup the daemon runs a lightweight consistency check (count of `agent_created` + `agent_spawned` events vs. count of registry rows). If they disagree, log a warning and recommend `/admin/rebuild-registry`. Don't auto-rebuild — the operator gets to decide.
4. **Port in use.** Fail loudly, print `lsof`-style hint, exit non-zero. The error message tells the operator how to kill the stale process or change the port.

### Deployment

Two shapes, same daemon binary:

- **Dev:** `uvicorn --reload`, frontend served by whatever the operator likes (vite dev server or file://).
- **Prod-on-operator's-box:** the daemon serves `frontend/` as static files at `/` and the API at `/v1/`. One process, one port, no CORS surprises.

Docker compose (Task #8) wraps this same binary. Bind mounts for `examples/`, `agents/`, `audit/`, `state/` so the container is stateless.

## Consequences

**Positive.**

- Single source of correctness. DNA hash, grading, constitution composition, audit hash linkage — all live in Python, all already tested. The frontend cannot drift from them because it doesn't implement them.
- Cheap to extend. Adding a new role, a new trait, a new event type is a Python change — the API surface is generic enough that the frontend only needs `/traits` and `/roles` to stay current.
- Offline by construction. No cloud dependency, no LLM provider coupling at the API boundary. The `llm-client.js` placeholder becomes a frontend concern (which model does the operator want their agents to run under?) and stops leaking into the daemon.
- Testable. Every endpoint is a function call away in `TestClient`. The existing `tests/unit/` suite grows a `tests/api/` sibling.

**Negative / costs accepted.**

- Operator has to start a process. Not ideal for someone who expects "open the HTML file." Mitigation: Docker compose + a `make serve` target hides this. Further mitigation: PyInstaller single-binary build is a future option if the manual `uvicorn` start becomes a barrier.
- One-lock-per-write concurrency model means parallel spawn operations serialize. Not a problem at single-operator scale; would be a problem in a hypothetical multi-tenant future. That future gets its own ADR.
- FastAPI adds three dependencies (fastapi, uvicorn, pydantic — the last is already transitively present). Accepted.

**Neutral.**

- Choosing FastAPI over Flask is a style preference here more than a capability one. FastAPI gives us Pydantic schema validation for free on every endpoint, which matches how we already model results as frozen dataclasses. Flask would have been fine. We are not re-litigating the web-framework debate in a future ADR unless we have a specific reason.

## Open questions

1. **Do we ship a Python client library (`forest_soul_forge.daemon.client`) alongside the daemon?** Cost is low and it makes scripting against a running daemon trivial for power users. Leaning yes but deferring until the API settles after Tasks #5 and #6.
2. **Where does agent runtime (actually invoking a model and recording findings) live?** Not this ADR. A worker process that consumes from a local queue and writes `finding_emitted` events is Phase 4+ territory.
3. **What happens to long-lived connections when the daemon restarts mid-operation?** For v0.1, every request is short. No sessions, no streaming. When Phase 4 introduces long-running agent work, that gets resumable via audit-chain replay rather than by holding HTTP connections open.
