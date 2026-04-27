# End-to-end smoke test — operator runbook

**Status:** Living doc; updated as new flows land.
**Last reviewed:** 2026-04-27.

This runbook walks through the full `forge → install → run` loop against
a live local stack — Ollama serving the local provider, Docker daemon
serving the FSF daemon, the vanilla-JS frontend in a browser. Use it
when you've just rebuilt the stack and want to verify nothing got broken
end-to-end.

The integration test in `tests/integration/test_full_forge_loop.py`
covers the same scenario without a real provider; this doc is for when
you need to verify the *real* path including the Ollama bridge.

## TL;DR — automated runner

If you just want to know "is everything working end-to-end right now":

```bash
$ ./scripts/live-smoke.sh
```

The script walks every stage below: daemon health → birth →
forge skill → install → run → recall → character-sheet check →
audit-chain check. It stops on the first failure, prints a
summary, and keeps the staged artifacts in `/tmp/fsf-live-smoke-*`
for diagnosis.

Exit codes: 0 pass, 1 fail (some stage broke), 2 prereq missing
(jq / curl / fsf not on PATH).

The rest of this runbook is the manual walk-through — read it when
you want to inspect a specific stage by hand or when the script
flags a failure you need to dig into.

## Prerequisites

- Ollama running with at least one model pulled (e.g. `ollama pull llama3.2`).
- Docker stack up — `docker compose up` from the repo root.
- `pip install -e .` in a venv so `fsf` is on PATH.
- An API token if `FSF_API_TOKEN` is set in the daemon's environment.

```bash
# Sanity-check the daemon is reachable.
$ curl -s http://127.0.0.1:7423/healthz | jq .ok
true
```

## Step 1 — birth an agent

Open `http://127.0.0.1:5173/` in the browser. On the Forge tab:

1. Pick the `network_watcher` role.
2. Leave traits at defaults.
3. Set `agent_name = SmokeWatcher`.
4. Click Birth.

Confirm the agent appears in the Agents tab with status `active`.

Keep this agent's `instance_id` handy — the Skills tab needs it.

## Step 2 — forge a skill

```bash
$ fsf forge skill "Compute a 1-hour time window from a relative expression."
```

Expected output (truncated):

```
[Skill Forge] proposing manifest via local...

  name:           get_window_smoke
  version:        1
  requires_tools: ['timestamp_window.v1']
  steps:          1
  skill_hash:     sha256:...

[Skill Forge] staged at:
  data/forge/skills/staged/get_window_smoke.v1/
```

Open `manifest.yaml` in your editor. Sanity check:

- `name`, `version`, `description` look right.
- `requires` lists `timestamp_window.v1`.
- `steps` has one entry calling `timestamp_window.v1`.
- `output` references something from the step (`${window.span_seconds}` or similar).

If the LLM picked a bad name or got the schema wrong, re-run with
`--name your_choice` or edit the manifest in place.

## Step 3 — install the skill

Until ADR-0031 T7 lands, install is a manual file copy:

```bash
$ cp data/forge/skills/staged/get_window_smoke.v1/manifest.yaml \
     data/forge/skills/installed/get_window_smoke.v1.yaml
```

Restart the daemon so the catalog loader picks it up:

```bash
$ docker compose restart daemon
```

(Round 2a of the next tranche will replace this with `fsf install skill`
+ `POST /skills/reload`.)

## Step 4 — verify the catalog

```bash
$ curl -s http://127.0.0.1:7423/skills | jq '.skills[] | {name, version, requires}'
{
  "name": "get_window_smoke",
  "version": "1",
  "requires": ["timestamp_window.v1"]
}
```

Or refresh the **Skills** tab in the frontend — the new card should appear.

## Step 5 — run the skill on the agent

In the frontend Skills tab:

1. Find the `get_window_smoke.v1` card.
2. Pick `SmokeWatcher` from the agent dropdown.
3. The session id pre-fills with a reasonable default; leave it.
4. The inputs textarea pre-fills `{"expr": ""}`. Edit to `{"expr": "last 15 minutes"}`.
5. Click **run**.

Expected: a green status line ending with `succeeded · executed=1
skipped=0`, plus an output JSON block with `start`, `end`, `span` (= 900).

Or via curl:

```bash
$ curl -s -X POST http://127.0.0.1:7423/agents/<INSTANCE_ID>/skills/run \
    -H 'Content-Type: application/json' \
    -H "X-FSF-Token: $FSF_API_TOKEN" \
    -H 'X-Idempotency-Key: smoke-1' \
    -d '{"skill_name":"get_window_smoke","skill_version":"1","session_id":"smoke-1","inputs":{"expr":"last 15 minutes"}}' \
  | jq .
```

## Step 6 — verify the audit chain

```bash
$ tail -7 audit/chain.jsonl | jq -r .event_type
skill_invoked
skill_step_started
tool_call_dispatched
tool_call_succeeded
skill_step_completed
skill_completed
```

Six entries in that exact order. If you see different order or missing
events, that's a regression — file an issue with the chain tail.

## Step 7 — check the character sheet

```bash
$ curl -s http://127.0.0.1:7423/agents/<INSTANCE_ID>/character-sheet \
  | jq '.stats'
{
  "not_yet_measured": false,
  "total_invocations": 1,
  "failed_invocations": 0,
  "total_tokens_used": null,
  "total_cost_usd": null,
  "last_active_at": "...",
  "per_tool": [
    {
      "tool_key": "timestamp_window.v1",
      "count": 1,
      "tokens": null,
      "cost": null
    }
  ]
}
```

`tokens` / `cost` are null because `timestamp_window.v1` is a pure
function. Once you forge a tool that wraps `provider.complete`, those
fields populate per call.

## What can go wrong

| Symptom                                        | Likely cause                                                      |
|------------------------------------------------|-------------------------------------------------------------------|
| `fsf forge skill` 0 replies                    | Ollama not running, or model not pulled. `ollama list`.           |
| `fsf forge skill` ManifestError                | LLM emitted invalid YAML — re-run, or use `--provider frontier`.  |
| `/skills` returns count=0                      | Daemon not restarted after install copy. `docker compose restart`.|
| `/skills/run` 503                              | `tool_dispatcher` failed at lifespan; check `/healthz` diagnostics.|
| Skill failed: `tool_refused` `genre_floor_violated` | Agent's genre forbids the tool's side_effects. Re-birth as a different genre. |
| Skill failed: `tool_pending_approval`          | Tool requires human approval. See Approvals tab to approve.       |

## After Round 2a + B3 land

Skill install:

```bash
$ fsf install skill data/forge/skills/staged/get_window_smoke.v1/
[Skill Forge install] copied manifest to data/forge/skills/installed/
[Skill Forge install] reloaded catalog → 1 skill
[Skill Forge install] audit_seq=N forge_skill_installed
```

Tool install (plugin mode, the new default):

```bash
$ fsf install tool data/forge/staged/my_tool.v1/
[Tool install] plugin staged at:
  data/plugins/my_tool.v1
[Tool install] audit_seq=M forge_tool_installed
[Tool install] reloaded daemon → 4 tools registered (1 plugin(s))
```

No daemon restart, no manual `tools/builtin/__init__.py` edit, audit-
chain entries recorded for both. Plugin tools live in
``data/plugins/<name>.v<version>/`` (spec.yaml + tool.py), discovered
by the lifespan loader and refreshed by ``POST /tools/reload``.

For dev work where you want the tool tracked in the source tree:

```bash
$ fsf install tool data/forge/staged/my_tool.v1/ --builtin
# legacy in-source path; daemon restart still required
```

## Cross-references

- ADR-0030 — Tool Forge
- ADR-0031 — Skill Forge
- ADR-0019 — Tool execution runtime (the dispatcher every step routes through)
- ADR-0007 — FastAPI daemon (the host)
- `tests/integration/test_full_forge_loop.py` — automated equivalent
