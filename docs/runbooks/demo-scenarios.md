# Demo scenarios — operator runbook

The repo ships pre-built demo scenarios so an operator can show the
forge end-to-end without driving every button by hand. Each scenario
is a snapshot of registry + audit-chain state that can be loaded into
either the real daemon or an isolated `demo/` directory.

## What scenarios exist

| Scenario | Size | What it shows |
|---|---:|---|
| `synthetic-incident` | ~1.5 MB | Full Security Swarm chain — 47 audit events, 4 nested `delegate.v1` calls (LogLurker → AnomalyAce → ResponseRogue → VaultWarden) |
| `fresh-forge` | tiny | Empty slate — no agents, no chain entries — drive Forge from scratch |
| `web-research-demo` | ~5 KB | Synthetic RFC + agent for the open-web demo (web_fetch + browser_action exercise) |

Each lives at `scenarios/<name>/` and ships its own `README.md`
with the per-scenario walkthrough.

## Load a scenario

```bash
./scenarios/load-scenario.command synthetic-incident
```

The command is interactive — you pick a target:

- **`prod`** (default): load into the top-level `data/` and
  `examples/` paths the daemon uses by default. Existing state gets
  archived to `.bak.<timestamp>` first.
- **`demo`**: load into the isolated `demo/` directory (start the
  daemon with `./start-demo.command` to point at this isolated dir;
  prod state stays untouched).

The two-mode design exists so you can rehearse against an isolated
demo while keeping your real agents and audit chain in place.

```bash
# Load isolated:
./scenarios/load-scenario.command synthetic-incident demo
./start-demo.command
# → daemon at 127.0.0.1:7423 with the demo state, your prod chain unchanged

# Load into prod (overwrites):
./scenarios/load-scenario.command synthetic-incident
./start.command
```

## Per-scenario walkthroughs

### `synthetic-incident`

The headline scenario. Demonstrates the canonical Security Swarm
escalation chain firing end-to-end. Loaded state includes:

- 9 swarm agents born (3 low / 3 mid / 3 high tier)
- 21 skill manifests installed
- A pre-seeded canary log file at the path `log_scan` watches
- The audit chain truncated to right before the chain fires

After loading, drive the chain with `./swarm-bringup.command`. The
script:

1. Health-checks the daemon
2. Verifies all 9 agents present
3. Triggers `signal_match.v1` on LogLurker against the canary log
4. Watches the chain fire through the full LogLurker → AnomalyAce
   → ResponseRogue → VaultWarden delegation
5. Prints the resulting 47-event audit chain summary

Presenter script: `scenarios/scripts/synthetic-incident.md`.

### `fresh-forge`

Empty starting state — no agents, no chain entries beyond
`chain_created`. The point of this scenario is to drive the **Forge**
from scratch:

1. Open the Forge tab in the frontend
2. Drag trait sliders, pick a role + genre
3. Click **Birth** — see the live audit event flow into the Audit tab
4. Birth a second agent under the same DNA → twin handling
5. Spawn a child agent → lineage chain
6. Forge a tool from English description (`fsf forge tool`)
7. Install it via `fsf install tool`
8. Use it from the agent

Presenter script: `scenarios/scripts/fresh-forge.md`.

### `web-research-demo`

The open-web tool family demo. Loaded state includes:

- A `web_observer` genre agent
- A synthetic RFC at `scenarios/web-research-demo/synthetic_rfc.md`
- `web_fetch.v1` + `trafilatura_extract.v1` (when the v0.2 batch ships)

The agent is asked to read the RFC, extract structured findings, and
write them to memory. Demonstrates the open-web reach + the
verified-memory tier (`memory_verify.v1`).

## Reset between scenarios

Cleanest reset between demos:

```bash
./reset.command          # archives generated state
./scenarios/load-scenario.command <next-scenario>
./start.command
```

`reset.command` archives `examples/audit_chain.jsonl`, `data/`, and
`soul_generated/` to `.bak.<timestamp>` so the next scenario starts
on a clean slate but the prior demo's state is recoverable.

## Build a new scenario

The pattern is:

1. Run a clean daemon, drive it through whatever state you want to
   capture
2. Stop the daemon
3. Copy the relevant subdirs into `scenarios/<your-name>/`:
   - `examples/audit_chain.jsonl` → `scenarios/<name>/audit_chain.jsonl`
   - `data/registry.sqlite` → `scenarios/<name>/registry.sqlite` (if
     you want pre-populated registry; otherwise the daemon rebuilds
     from the chain)
   - Any `soul_generated/<x>.soul.md` + `.constitution.yaml` →
     `scenarios/<name>/data/soul_generated/`
4. Add a `scenarios/<name>/README.md` describing what to do after load
5. Wire it into `scenarios/load-scenario.command`'s case statement

The `synthetic-incident` and `fresh-forge` directories are reference
templates — copy their structure.

## Where to dig deeper

- `scenarios/README.md` — top-level scenarios index
- `scenarios/scripts/*.md` — presenter scripts (what to say while
  demoing each scenario)
- `scenarios/load-scenario.command` — the loader script
- `start-demo.command` — the demo-mode daemon launcher
- `reset.command` — state archiver
