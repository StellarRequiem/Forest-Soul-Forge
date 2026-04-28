# Security Swarm bring-up

ADR-0033 Phase D + E. Walks the operator from a freshly-built daemon to all 9 swarm agents born, all 21 skill manifests installed, and the canonical chain proven via the synthetic-incident smoke test.

## Prerequisites

- The daemon is up at `$FSF_DAEMON_URL` (default `http://127.0.0.1:7423`). Check `/healthz`.
- `jq` and `curl` on `PATH`.
- The 31 builtin tools have registered cleanly. Verify on `/healthz` — `tool_runtime` should report `status: ok`.
- (Optional, for privileged tools to actually fire) The sudo helper installed per `docs/runbooks/sudo-helper-install.md`, and `FSF_ENABLE_PRIV_CLIENT=true` set in the daemon's environment. Without this, `isolate_process.v1` and `dynamic_policy.v1` refuse cleanly with "no PrivClient wired" — the smoke test still runs, just won't actually kill processes.

## Step 1 — Birth the 9 agents

```bash
./scripts/security-swarm-birth.sh
```

Calls `POST /birth` once per role: PatchPatrol, Gatekeeper, LogLurker (security_low); AnomalyAce, NetNinja, ResponseRogue (security_mid); ZeroZero, VaultWarden, DeceptionDuke (security_high). Default `enrich_narrative=false` for fast, deterministic births — set `FSF_ENRICH=true` if you want LLM-authored `## Voice` stanzas in each soul.md (slower, requires Ollama up).

Output one line per agent: `OK <role> <name> instance=<id> dna=<hash>`. If any birth fails, the script exits non-zero with a partial report.

## Step 2 — Install the 21 skill manifests

```bash
./scripts/security-swarm-install-skills.sh
```

Copies every YAML in `examples/skills/` into the daemon's `skill_install_dir` (default `data/forge/skills/installed/`) and triggers `POST /skills/reload` so the catalog picks them up without a daemon restart.

The 21 skills decompose as: 4 chain skills (`morning_sweep`, `investigate_finding`, `contain_incident`, `key_audit`) + 17 supporting skills across all 9 agents.

## Step 3 — Run the synthetic-incident smoke test

```bash
./scripts/security-smoke.sh
```

Seeds a synthetic log file with a recognisable anomaly pattern, drives `LogLurker.morning_sweep` against it, and inspects the audit chain to verify each chain link fired:

- `tool_invoked` events for each step in the chain
- `agent_delegated` events for the cross-agent handoffs (`log_lurker → anomaly_ace → response_rogue → vault_warden`)
- `tool_call_pending_approval` for the `isolate_process` call (acceptable to be zero if the triage severity falls below the configured floor — in that case the chain stops cleanly at containment without needing operator approval)

The smoke does NOT require the privileged helper to be installed — `isolate_process` will refuse cleanly and the chain stops there. With the helper wired, the chain reaches the approval queue and waits for operator action.

## Step 4 — Run the integration test under pytest

```bash
pytest tests/integration/test_security_swarm_smoke.py -v
```

Reproduces the smoke test in pytest form, so it can run in CI alongside the rest of the suite. Asserts on chain shape rather than on real PID-killing behavior — the test passes whether or not `FSF_ENABLE_PRIV_CLIENT=true`, because it observes the audit chain shape, not the privileged side effect.

## Verifying it ran

After Step 3 completes successfully:

- `GET /agents` returns 9 swarm agents in their respective genres.
- `GET /skills` returns the 21 swarm manifests by name.
- `GET /audit?limit=200` shows the chain of `tool_invoked` + `agent_delegated` entries from the smoke run.
- The frontend's Memory tab (per-agent) shows the lineage-scoped writes that flowed through the chain.
- Per-tier memory ceilings hold: low/mid wrote at `lineage`, high wrote at `private` unless explicit `memory_disclose` consent fired.

## Common failures and what they mean

- `agents/{id}/skills/morning_sweep.v1/run` 404 — skills weren't installed; rerun Step 2.
- Empty `agent_delegated` event count — the chain didn't escalate; check that `escalate_threshold` is reached (synthetic log has 3 matches, default threshold is 3, so equality → trigger; if you tweak the seeded log, also tweak the threshold).
- `isolate_process.v1: no PrivClient wired` — expected on a daemon without the helper; not a smoke failure.
- `pf-add` permission denied — helper installed but sudoers rule wrong; check `/etc/sudoers.d/fsf` matches the runbook.

## Next: promote ADR-0033 to Accepted

When the smoke passes end-to-end, edit `docs/decisions/ADR-0033-security-swarm.md` and change `Status: Proposed` to `Status: Accepted` with a date. Drop a note in `docs/audits/YYYY-MM-DD-phase-b-d-e-review.md` summarizing what shipped vs the plan.
