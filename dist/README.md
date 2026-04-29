# Distribution build

Versioned `.zip` archives for shipping to evaluators who don't `git clone`.

## Build

```bash
./dist/build.command
```

Wraps `git archive` so the .zip respects `.gitignore` + `.gitattributes`.
Output lands at `dist/forest-soul-forge-<short-sha>-<yyyymmdd>.zip`.

The build script:
1. Verifies you're in a git repo
2. Warns (doesn't block) on uncommitted or untracked changes
3. Runs `git archive --prefix=<name>/ -o <name>.zip HEAD`
4. Spot-checks that critical entry points are inside (start.command,
   start-demo.command, load-scenario.command, the synthetic-incident
   audit chain + registry, README, pyproject, daemon/app.py)
5. Reports the size + path + handoff instructions

## What ships in the .zip

Everything in git, prefixed with `<name>/` so it unzips into a clean
directory:

- `src/` — daemon + tools + skills + memory + everything Python
- `frontend/` — vanilla-JS UI (no build step)
- `config/` — trait_tree, genres, tool_catalog, constitution_templates
- `examples/` — committed soul.md / constitution.yaml + the canonical
  500-event audit chain
- `scenarios/` — pre-built data states + presenter scripts
- `scripts/` — bash helpers (live-smoke, swarm-bringup, etc.)
- `docs/` — ADRs, runbooks, audit docs, vision notes
- `tests/` — unit + integration tests
- All `.command` files (start, stop, reset, start-demo, run,
  swarm-bringup, push, etc.)
- Top-level docs: README.md, STATE.md, LICENSE, pyproject.toml

## What does NOT ship

Excluded automatically by `.gitignore`:

- `.git/` (git internals — recipient gets a clean directory, not a repo)
- `.venv/` (rebuilt by start.command on first run)
- `__pycache__/`, `*.pyc`
- `data/` runtime state (recipient doesn't want our data)
- `demo/` (same)
- `*.bak.*` backup files
- Prior `dist/forest-soul-forge-*.zip` builds (no recursive bundling)
- `node_modules/` (n/a but defensively excluded)

## Recipient flow

1. Receive `forest-soul-forge-<sha>-<date>.zip`
2. Unzip wherever (creates `forest-soul-forge-<sha>-<date>/`)
3. Double-click `start.command`
   - ~30s first run (Python check, venv, pip-install)
   - ~5s subsequent runs
4. Browser opens to the Forge UI at `127.0.0.1:5173`

For an out-of-box demo experience:

```bash
cd forest-soul-forge-<sha>-<date>
./scenarios/load-scenario.command synthetic-incident demo
./start-demo.command
```

The browser opens to the Forge with the canonical 47-event swarm
chain pre-loaded under the isolated `demo/` dir. Walk
`scenarios/scripts/synthetic-incident.md` for the beat-by-beat
narrative.

## Future work (not in F8)

- **Codesigning + notarization** for macOS Gatekeeper. Would let the
  user double-click `start.command` from a .zip downloaded via
  browser without the "downloaded from internet" warning. Requires
  an Apple Developer account.
- **`.dmg` bundle** with drag-to-Applications UX. Heavier than the
  `.zip` but more familiar for non-technical evaluators.
- **Bundled venv** so the recipient doesn't need Python ≥3.11 on
  PATH. Would push the .zip to ~150 MB but eliminate the bootstrap
  failure mode.
- **Auto-update checker** — start.command could hit a GitHub
  releases endpoint and notify when a newer .zip is available.
