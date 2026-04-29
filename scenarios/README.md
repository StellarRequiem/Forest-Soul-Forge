# Demo scenarios

Pre-built data states that drop you into a specific narrative without
having to run swarm-bringup or birth agents from scratch. Use these
when rehearsing a demo, evaluating the product cold, or recovering
quickly after a `reset.command` clears your working state.

## Loading a scenario

```bash
./scenarios/load-scenario.command                         # interactive picker
./scenarios/load-scenario.command synthetic-incident      # direct
```

The launcher stops the running daemon, archives your current state
(same `.bak.<timestamp>` rename pattern as `reset.command`), copies
the scenario into place, then prompts you to double-click
`start.command` to bring the stack up.

## Available scenarios

### `synthetic-incident/` — the headline story
The full ADR-0033 Phase D+E run frozen at the moment of completion.
~199 agents in the registry across every role family, plus the canonical
500-event audit chain culminating in the 47-event swarm chain
(`LogLurker → AnomalyAce → ResponseRogue → VaultWarden`) at seqs
453-499. **This is the demo to lead with for security pros.**

Presenter script: [`scripts/synthetic-incident.md`](scripts/synthetic-incident.md)

### `fresh-forge/` — empty slate
A blank registry and a chain with only the `chain_created` genesis
entry. Optimal for "let me show you how to forge an agent from scratch."
Walks an evaluator through the Birth flow with no existing state to
distract.

Presenter script: [`scripts/fresh-forge.md`](scripts/fresh-forge.md)

## Adding your own scenarios

A scenario is a directory under `scenarios/` containing any subset of:

```
scenarios/<name>/
├── audit_chain.jsonl     # top-level — the daemon's canonical chain
├── registry.sqlite       # top-level — the derived index (rebuildable)
├── registry.sqlite-wal   # SQLite write-ahead log (optional)
├── registry.sqlite-shm   # SQLite shared-memory file (optional)
└── data/
    └── soul_generated/   # one .soul.md + .constitution.yaml per agent
```

The launcher copies whatever files are present; missing files mean the
daemon will create them at next boot (a missing chain becomes a fresh
genesis, a missing registry gets rebuilt from the chain).

Capture a scenario from your current state with:

```bash
NAME=my-scenario
mkdir -p scenarios/$NAME/data
cp audit_chain.jsonl scenarios/$NAME/
cp registry.sqlite scenarios/$NAME/
cp -r data/soul_generated scenarios/$NAME/data/
```

If the scenario benefits from a presenter script, add
`scenarios/scripts/<name>.md` and the launcher will surface it on load.

## Notes

- Scenarios ship in git so a fresh checkout has demo-ready state.
  The synthetic-incident snapshot is ~1.6MB total.
- `data/ollama/` (the local LLM model store, multi-GB) is never copied
  in or out — those models live on your machine, not in scenarios.
- Loading a scenario archives your current state via the
  `.bak.<timestamp>` pattern. Recover by renaming the backups back.
