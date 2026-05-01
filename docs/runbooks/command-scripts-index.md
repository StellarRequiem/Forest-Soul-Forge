# `.command` script index

The repo ships 37 macOS double-click `.command` scripts. This index
groups them by purpose so operators can find the right one without
guessing from filenames.

## Lifecycle

| Script | What it does |
|---|---|
| `start.command` | Bootstrap venv on first run + launch daemon + frontend. **The recommended first-run entry point.** |
| `start-demo.command` | Same as `start.command` but reads/writes the isolated `demo/` directory. Use for rehearsals — your prod state stays untouched. |
| `run.command` | Direct launch (skips bootstrap). Use after the venv is built. |
| `stop.command` | Kill any process bound to ports 7423 + 5173. |
| `reset.command` | Archive all generated state to `.bak.<timestamp>` and start fresh. |

## Docker stack

| Script | What it does |
|---|---|
| `docker-up.command` | Start daemon + frontend via Docker Compose. Add `--profile llm` for an Ollama container. |
| `stack-rebuild.command` | Rebuild both Docker containers `--no-cache`. |
| `frontend-rebuild.command` | Rebuild only the frontend container. |

## Ollama lifecycle

| Script | What it does |
|---|---|
| `ollama-up.command` | Start Ollama. |
| `ollama-coder-up.command` | Start Ollama with the coder-track model preloaded. |
| `ollama-status.command` | Print which Ollama models are loaded + their memory footprint. |
| `kill-ollama.command` | Kill any running Ollama process. |

## Distribution + git ops

| Script | What it does |
|---|---|
| `push.command` | `git push origin main`. |
| `clean-git-locks.command` | Clear stale `.git/index.lock` + `HEAD.lock` + `tmp_obj_*` files (created during this audit when the harness sandbox couldn't remove them itself). |
| `a5-finalize.command` | Phase A.5 push helper. Runs the standard A.5 checklist + pushes accumulated commits. |

## Tests

| Script | What it does |
|---|---|
| `run-tests.command` | Run the full pytest suite in the Docker test container. |
| `run-tests-direct.command` | Run pytest directly on the host (faster; needs deps already installed). |
| `t4-tests.command` | Tool-runtime T4 (per-call accounting) targeted tests. |

## Live tests (operator-driven smoke)

These drive a running daemon end-to-end. Each one expects the daemon
+ Ollama to be up. Their output is the ground truth for "does this
phase actually work."

| Script | Phase | What it verifies |
|---|---|---|
| `live-fire-voice.command` | foundation | Birth a real agent end-to-end |
| `live-test-r2.command` | R-track | R2 birth_pipeline extraction |
| `live-test-r4.command` | R-track | R4 registry table split |
| `live-test-r-rebuild.command` | R-track | Registry rebuild from chain |
| `live-test-t2-tier.command` | T-track | T2 task_caps + posture_overrides |
| `live-test-k4.command` | K-track | K4 mcp_call.v1 |
| `live-test-k6.command` | K-track | K6 hardware binding lifecycle |
| `live-test-g6-k5.command` | G/K | G6 suggest_agent + K5 chronicle |
| `live-test-sw-coding-tools.command` | SW-track | code_read + code_edit + shell_exec |
| `live-test-sw-coding-triune.command` | SW-track | Atlas/Forge/Sentinel handoff |
| `live-triune-file-adr-0034.command` | SW-track | Meta-demo: agents file ADR-0034 |
| `live-test-y2-conversation.command` | Y-track | Y2 single-agent conversation |
| `live-test-y3-multi-agent.command` | Y-track | Y3 @mention chain |
| `live-test-y-full.command` | Y-track | Full Y1-Y7 conversation runtime |

## Special-purpose

| Script | What it does |
|---|---|
| `swarm-bringup.command` | Full ADR-0033 Phase D+E walkthrough — births 9 swarm agents, installs 21 skills, drives synthetic-incident, verifies 47-event chain. |
| `soak.command` | Soak / stress test — runs many concurrent dispatches against the daemon to catch race conditions. |
| `sw-debug.command` | SW-track debug helper — dumps the most recent SW-track audit chain entries. |

## Naming conventions

- **`live-test-*`** — assertive smoke runners. Exit nonzero if the
  expected chain shape doesn't materialize.
- **`live-fire-*`** — interactive / operator-paced runs. Often print
  intermediate state for visual inspection.
- **`live-triune-*`** — triune-specific ceremonies + meta-demos.
- **`*-up.command` / `kill-*.command`** — process lifecycle.
- **`*-rebuild.command`** — container or state regeneration.

There's some legacy drift (`live-test-r-rebuild` mixes patterns)
documented in the audit but not yet renamed. Don't rename without a
matching update to scripts/docs that reference them.

## Where to dig deeper

- `scenarios/load-scenario.command` — scenario loader (lives outside
  the top-level `.command` set because it's interactive)
- `scripts/security-swarm-birth.sh` — shell scripts under `scripts/`
  are the building blocks; `.command` scripts at root wrap them with
  Finder-launchable `.command` semantics
- `docs/runbooks/end-to-end-smoke-test.md` — the canonical
  end-to-end test walkthrough using these scripts
