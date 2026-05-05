# Headless install — Forest kernel without SoulUX

ADR-0044 Phase 3 (Burst 130, 2026-05-05).

This runbook documents how to install and run the Forest kernel
(the FastAPI daemon + CLI) without any of the SoulUX reference
distribution's userspace artifacts (frontend, Tauri shell, dist
helpers). External integrators, second distributions, and CI/CD
pipelines target this path.

## When to use this

- You're building a different distribution on top of Forest (a
  terminal-only TUI, a server-headless deployment, a mobile shell,
  etc.) and need the kernel package alone.
- You're integrating Forest's governance + audit kernel into a
  larger system that already has its own UX.
- You want to run Forest in CI/CD where the browser frontend would
  be dead weight.
- You're contributing to the kernel and want to verify the
  kernel/userspace boundary holds (no implicit dependency on
  `frontend/` or `apps/desktop/`).

## When to use SoulUX instead

If you want the polished operator experience (Tauri desktop shell,
forge UI, agent dashboard, audit timeline browser), install the
SoulUX flagship instead — see `README.md` quickstart. SoulUX is
still the recommended path for first-time exploration.

## Three install paths

### Path 1 — pip install from source (kernel-only)

```bash
git clone https://github.com/StellarRequiem/Forest-Soul-Forge.git
cd Forest-Soul-Forge

# Install kernel + daemon extras only. No frontend assets fetched.
pip install -e ".[daemon]"

# Boot the kernel daemon.
python -m forest_soul_forge.daemon
# Daemon now serving on 127.0.0.1:7423.
# OpenAPI at /docs, healthz at /healthz.
```

The kernel's data lives under `data/` by default — registry SQLite,
audit chain JSONL, generated agent artifacts. Override paths via
the `FSF_*` env vars in `daemon/config.py`.

The CLI is also installed:

```bash
fsf agent posture get <agent-id>
fsf plugin list
fsf chronicle full-chain
```

See `docs/spec/kernel-api-v0.6.md` §6 for the full CLI surface.

### Path 2 — Docker (kernel-only)

```bash
git clone https://github.com/StellarRequiem/Forest-Soul-Forge.git
cd Forest-Soul-Forge

# Bring up ONLY the daemon — frontend service stays down.
docker compose up daemon

# Optional: + Ollama for local-first model inference.
docker compose --profile llm up daemon ollama
```

`docker compose up daemon` is the canonical headless invocation.
The `frontend` service (SoulUX reference UI) does not start; the
kernel runs without it.

For deployments where you want the kernel exposed beyond loopback,
edit `docker-compose.yml`'s `127.0.0.1:7423:7423` mapping AND set
`FSF_API_TOKEN` in `.env`. Do not do the former without the latter.

### Path 3 — PyInstaller daemon binary

ADR-0042 T4 ships a single-file daemon binary via
`dist/build-daemon-binary.command`. The binary is what SoulUX
bundles as a Tauri sidecar, but it's also the cleanest way to get
a no-Python-install kernel running on a target machine.

```bash
# From a checkout, build the binary:
./dist/build-daemon-binary.command
# Output: dist/build/forest-soul-forge-daemon

# Run it directly — no Python install needed on the target host:
./dist/build/forest-soul-forge-daemon
```

The binary embeds the kernel package + uvicorn; everything the
daemon needs is statically bundled. See ADR-0042 T4 for the
rationale.

## Sanity-check the headless install

After bringing up the daemon, verify the kernel works without UI:

```bash
# 1. Health.
curl -s http://127.0.0.1:7423/healthz | jq '.status'
# Expected: "ok"

# 2. Read endpoints respond.
curl -s http://127.0.0.1:7423/genres   | jq '.genres | keys'
curl -s http://127.0.0.1:7423/tools    | jq '.tools[].name' | head -5
curl -s http://127.0.0.1:7423/agents   | jq '.agents | length'

# 3. Audit chain is being written.
curl -s "http://127.0.0.1:7423/audit/tail?n=5" | jq '.events[].event_type'
```

If all four return without errors, the kernel is up and the
boundary holds. The full kernel API surface (52 endpoints) is in
`docs/spec/kernel-api-v0.6.md` §5.

## Authentication for write endpoints

By default, the kernel daemon does not require auth for read or
write endpoints — local-first defaults assume the operator trusts
the local machine.

For deployments that face untrusted networks (or you just want
defense-in-depth):

```bash
# Set a token in .env:
echo "FSF_API_TOKEN=<random-32-char-string>" >> .env

# All write endpoints now require X-FSF-Token header:
curl -X POST http://127.0.0.1:7423/agents/{id}/posture \
  -H "X-FSF-Token: <random-32-char-string>" \
  -H "Content-Type: application/json" \
  -d '{"posture": "yellow"}'
```

The CLI auto-includes the token via `$FSF_API_TOKEN` env var (or
`--api-token <value>` flag). See `docs/spec/kernel-api-v0.6.md`
§5.1 for the full auth model.

## CORS

The default `cors_allow_origins` includes the SoulUX reference
frontend's port (5173). Headless installs that don't need browser
access can tighten this:

```bash
echo "FSF_CORS_ALLOW_ORIGINS=" >> .env  # empty list disables CORS entirely
```

Or restrict to your specific consumer's origin:

```bash
FSF_CORS_ALLOW_ORIGINS=https://my-distribution.example.com
```

## What's NOT in the kernel package

If you `pip install forest-soul-forge[daemon]`, you do NOT get:

- `frontend/` — the SoulUX reference UI (vanilla JS + nginx)
- `apps/desktop/` — the SoulUX Tauri 2.x desktop shell
- `dist/` — SoulUX-distribution build helpers
- Repo-root `*.command` SoulUX operator scripts (`start.command`,
  etc.)
- `dev-tools/commit-bursts/` — developer history (per-burst commit
  scripts; not API surface)

These are SoulUX-distribution artifacts. A different distribution
provides its own equivalents (or omits them entirely for
server-headless deployments).

What you DO get:

- `src/forest_soul_forge/` — the entire kernel Python package
- `config/*.yaml` — the schema YAMLs (kernel-adjacent; values are
  operator-customizable)
- `examples/audit_chain.jsonl` + `examples/skills/*` — kernel-
  adjacent seed state (the live audit chain default lives here, see
  `daemon/config.py`)
- `docs/spec/kernel-api-v0.6.md` — the contract-grade spec

## References

- ADR-0044 — Kernel Positioning + SoulUX Flagship Branding (the
  parent strategic decision)
- ADR-0044 P3 — True headless mode + SoulUX frontend split (this
  runbook delivers the headless half)
- `docs/spec/kernel-api-v0.6.md` — formal kernel API spec; this
  runbook references it for endpoint catalogs
- `docs/architecture/kernel-userspace-boundary.md` — directory-
  level boundary map
- `KERNEL.md` — root-level kernel/userspace ABI summary
