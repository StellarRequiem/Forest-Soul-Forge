# ADR-0032 — CLI architecture

- **Status:** Proposed
- **Date:** 2026-04-27
- **Related:** ADR-0007 (FastAPI daemon — the CLI talks to the same primitives), ADR-0008 (model provider — CLI builds providers locally), ADR-0030 (Tool Forge — first CLI surface), ADR-0031 (Skill Forge — second CLI surface), ADR-0024 (horizons — CLI is part of H1 ergonomics).

## Context

ADR-0030 and ADR-0031 introduced the first CLI surfaces (`fsf forge tool`, `fsf forge skill`). More are coming: `fsf agents list`, `fsf audit verify`, `fsf benchmark run`, `fsf provider switch`, etc. Without a structural decision now, every new subcommand will reinvent argument parsing, provider construction, and exit-code conventions.

This ADR locks in the architecture so future subcommands compose with the existing ones rather than fighting them.

The CLI matters for three reasons:

1. **Operator workflows** that don't fit a frontend (scripted forging, batch agent operations, CI integrations).
2. **Headless deployments** where running a daemon is overkill — a local operator who just wants to forge a tool shouldn't need a web server.
3. **Trustworthy mutations.** Anything that changes durable state (artifacts, registry, audit chain) must hit the same machinery the daemon does. The CLI cannot be a back-door around the audit-evident discipline.

## Decision

### One entry point, hierarchical subcommands

Single `fsf` console-script declared in `pyproject.toml`'s `[project.scripts]`. All subcommands hang off it:

```
fsf forge tool "..."          # ADR-0030
fsf forge skill "..."         # ADR-0031
fsf agents list               # future
fsf agents archive <id>       # future — touches audit chain
fsf audit verify              # future
fsf benchmark run <suite>     # future (ADR-0023)
fsf provider switch frontier  # future
```

Layout: `<verb> <noun> [args]`. Verb-first matches `git`, `kubectl`, and operator muscle memory. The `forge` subcommand has its own sub-tree because it produces *artifacts* (tools, skills) — different things, same authoring pattern.

Single entry point means:

- One install, one `fsf` on PATH.
- Tab completion (post-T2 feature) works across the whole surface.
- New subcommands register in one place (`cli/main.py`).

### Argparse, not click/typer

Stdlib `argparse`. No third-party CLI dep. Reasons:

- Project keeps dependencies minimal; the daemon's optional extras (`[daemon]`) already pull in fastapi/pydantic. The CLI shouldn't add another tier of mandatory deps.
- argparse is verbose but stable; click/typer churn faster and have decorator-magic that hides what the parser actually does.
- Code-review cost: a future maintainer reading `argparse.ArgumentParser(...)` calls knows exactly what they do. Decorator-based libs hide the parser shape behind `@click.command()` and `@click.option(...)`.

If the verbosity becomes painful past ~5 subcommands, revisit.

### File layout

```
src/forest_soul_forge/cli/
  __init__.py
  main.py             — root parser + subcommand dispatch
  forge_tool.py       — `fsf forge tool` runner (ADR-0030)
  forge_skill.py      — `fsf forge skill` runner (ADR-0031)
  agents.py           — `fsf agents ...` (future)
  audit.py            — `fsf audit ...` (future)
  ...
  _common.py          — shared helpers (provider construction, etc.)
```

One file per top-level verb (or per sub-tree like `forge`). Dispatch happens in `main.py`'s `_build_parser`; each subparser has a `set_defaults(_run=<callable>)` that `main()` invokes.

### Provider construction without the daemon

The CLI does **one-shot operations**. It does NOT depend on a running daemon. For ops that need an LLM (Tool Forge, Skill Forge), the CLI builds a `ModelProvider` directly from `DaemonSettings`:

```python
def _build_provider(override):
    settings = build_settings()
    if override == "local":
        return LocalProvider(...)
    if override == "frontier":
        return FrontierProvider(...)
    return _build_provider(settings.default_provider)
```

That gives the CLI:

- Access to the same env-var / config story as the daemon.
- Independence from daemon process state — `fsf forge tool` works on a machine with no daemon running.
- Same provider abstractions, same `task_kind` routing, same model maps.

`_build_provider` lives in `cli/_common.py` so every subcommand calls it the same way.

### Read-only vs. mutating operations

CLI operations split into two classes:

| Class       | Examples                                          | Goes through audit chain? | Talks to daemon? |
|-------------|---------------------------------------------------|---------------------------|------------------|
| Read-only   | `fsf forge tool` (writes only to staged/), `fsf agents list`, `fsf audit verify` | Optionally (analysis events) | No — direct registry/disk reads |
| Mutating    | `fsf agents archive`, `fsf provider switch` (when persisted), `fsf forge tool --install` (T4) | **Yes — required**     | Optionally (HTTP) or direct |

**Mutating ops MUST emit audit-chain entries.** The CLI is not a back-door. If a future `fsf agents archive` lands without an `agent_archived` audit event, the daemon's chain won't reflect the operator action — that's an architectural break.

Two viable paths for mutating ops:

1. **Direct mode**: CLI opens the AuditChain + Registry directly (same single-writer discipline). Works when no daemon is running. Risk: if the daemon IS running, two writers race on SQLite. Mitigation: the CLI checks for a running daemon first (look for the FastAPI port + healthcheck) and refuses direct mode if found.

2. **Via daemon mode**: CLI POSTs to the running daemon's endpoint. Same idempotency-key + token machinery as the frontend. Cleaner; only works when the daemon is up.

**Default behavior:** mutating CLI ops try the daemon first (HTTP), fall back to direct mode if no daemon is running. The choice is logged in the audit-chain entry as `mode: cli_via_daemon` or `mode: cli_direct`.

For T1 of ADR-0030, Tool Forge writes only to `data/forge/staged/` — that's read-only as far as canonical artifacts are concerned (the registry/audit chain are unaffected). Installation (ADR-0030 T4) is the first CLI mutation; the audit-chain story above kicks in then.

### Exit codes

| Code | Meaning                                         |
|------|-------------------------------------------------|
| 0    | success                                         |
| 1    | operation failed (bad input, provider error, hard analysis flag without --force, etc.) |
| 2    | usage error (missing args, unknown subcommand)  |
| 130  | interrupted (Ctrl-C); equals 128 + SIGINT       |

Argparse's default for usage error is 2; we keep it. We use 1 for "ran but didn't succeed" so scripts can distinguish "bad command" from "command rejected the input."

### Output discipline

- **Human-facing output goes to stdout.** Single-line "happy path" results, file paths, etc.
- **Diagnostic output goes to stderr.** "[Tool Forge] proposing..." prefixes, warnings, errors.
- Operator can `fsf forge tool ... > /dev/null` and still see the diagnostic chatter.
- Output is plain text; no ANSI by default. T3 may add `--color auto/always/never` if there's demand.

### Configuration

CLI reads the same env vars as the daemon (`FSF_TRAIT_TREE_PATH`, `FSF_DEFAULT_PROVIDER`, etc.) via `DaemonSettings.build_settings()`. No separate `.fsfrc`. This matches local-first: one config story, applied wherever it's needed.

For the API token (`FSF_API_TOKEN`) used when the CLI talks to a running daemon: also via env var, not a config file. CI/CD operators set it in their shell; interactive operators export it once per session.

### Subcommand registration pattern

Adding a new subcommand:

1. Create `cli/<verb>.py` with `def run(args: argparse.Namespace) -> int`.
2. Edit `cli/main.py`'s `_build_parser`:
   ```python
   verb = sub.add_parser("verb", help="...")
   verb_sub = verb.add_subparsers(dest="<verb>_cmd")
   verb_sub.required = True
   noun = verb_sub.add_parser("noun", help="...")
   noun.add_argument(...)
   noun.set_defaults(_run=lambda args: _run_via("verb"))
   ```
3. Test via `python -m forest_soul_forge.cli.main verb noun ...`.
4. After install, available as `fsf verb noun ...`.

The `set_defaults(_run=...)` pattern is what `main()` dispatches on — adding a new subcommand never requires touching the dispatcher.

### What the CLI is NOT

- **Not a TUI.** No interactive menus past the y/N confirmations Tool Forge uses. If a workflow needs richer interaction, it belongs in the frontend.
- **Not a daemon-replacement.** Long-running services (model serving, the FastAPI host, the scheduled-task runner) live in the daemon. The CLI is one-shot operations only.
- **Not silent.** Mutating operations log to stderr what they did, including audit-chain seq numbers. The audit-evident discipline isn't a frontend feature; it applies to CLI too.

## Trade-offs and rejected alternatives

**One root command vs. multiple commands** (`fsf-forge`, `fsf-agents`, etc.). Rejected. Tab completion fragments, install footprint grows, operators have to remember which dash-name does what.

**Click/Typer vs. argparse.** Rejected for the dependency-budget reason. Typer's decorator ergonomics are nice but not nice enough to take on a maintained dep.

**Heavy decoupling**: a `cli/`-layer plus a `cli_handlers/`-layer that the GUI also calls. Rejected for now. The "shared handler" abstraction is best earned, not designed up front. Right now the daemon endpoints are the shared surface; CLI calls the same primitives directly. Refactor if the duplication actually grows.

**Persistent shell session** (REPL-style `fsf shell`). Rejected for v1. Not needed for any current workflow; reconsider when there's a clear use case (e.g. forge → review → install round-trips without typing the whole tool name three times).

**JSON output mode** for scripting. Deferred. Add `--json` per subcommand when a real consumer needs it. Don't speculatively design for a CI/CD operator that doesn't exist yet.

## Consequences

**Positive.**
- Adding a subcommand is mechanical, not architectural.
- One install, one binary on PATH, one config story.
- Mutating ops cannot accidentally bypass the audit chain — the architecture forces the question "who writes the entry?"
- Tab completion (post-T2 feature) works for free once we add `argcomplete`.
- The CLI works without the daemon running; the daemon works without the CLI.

**Negative.**
- Argparse verbosity scales linearly with subcommand count. Past ~10 nouns we reconsider.
- Stdout/stderr discipline is a convention, not a compiler-enforced rule. Easy to break in a hurry.
- Direct-mode mutation needs the "is the daemon running?" check, which is OS-specific (port probe + healthcheck). Imperfect.

**Neutral.**
- Operator-facing docs live in `--help` output. The ADR is the architecture; runbooks belong elsewhere.

## Cross-references

- ADR-0007 — daemon + endpoint structure (CLI talks to the same primitives)
- ADR-0008 — model provider (CLI builds locally from settings)
- ADR-0030 — Tool Forge (first CLI surface)
- ADR-0031 — Skill Forge (second CLI surface)
- ADR-0024 — horizons (CLI is H1 ergonomics)
