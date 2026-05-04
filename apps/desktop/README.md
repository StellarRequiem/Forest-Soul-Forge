# Forest Soul Forge — desktop shell

ADR-0042 T3 (Burst 99). The Tauri 2.x packaging that wraps the
existing daemon + frontend into a signed desktop app per the v0.5
product direction.

This directory holds:

```
apps/desktop/
├── Cargo.toml          # Rust crate spec
├── tauri.conf.json     # Tauri runtime config (window, bundle, identifier)
├── build.rs            # Tauri build glue
├── src/main.rs         # Shell entry point (spawns daemon + opens window)
└── icons/              # Bundle icons (placeholders for v0.5 dev)
```

## What's working as of Burst 99

- **Crate compiles** with `cargo check` (assuming Rust + Tauri
  CLI are installed).
- **Dev mode runs**: `cargo tauri dev` spawns the daemon as a
  Python subprocess, opens a window pointing at the existing
  `frontend/` directory, and shuts the daemon down on quit.
- **Production builds need T4 first.** The shell currently
  invokes `python3 -m forest_soul_forge.daemon` as a sidecar,
  which assumes the host has Python 3.11+ and the package
  installed. Distributing this to users-without-Python requires
  bundling the daemon as a binary — that's Burst 101 (T4).

## What's NOT working yet

- **Bundle build fails on icons.** `cargo tauri build` errors
  out without real icons in `icons/`. Fix: run
  `cargo tauri icon <source.png>` once a 1024×1024 source PNG
  exists. Tracked in `icons/README.md`.
- **No code signing.** macOS notarization needs an Apple
  Developer account ($99/yr); Windows code-signing needs a
  cert ($200-500/yr). Configured in T5 (Burst 102-103).
- **No auto-updater.** Tauri's `tauri-plugin-updater` is added
  in T5 alongside the manifest hosting decision (GitHub
  Releases vs CDN vs custom server).

## Operator: running dev mode

One-time setup on the host:

```bash
# Install Rust toolchain (if not present)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Install Tauri CLI
cargo install tauri-cli --version "^2.0"

# Install Python deps for the daemon
cd <repo-root>
pip install -e .
```

Run dev mode:

```bash
cd apps/desktop
cargo tauri dev
```

This will:

1. Spawn `python3 -m forest_soul_forge.daemon --port 7423` as a
   subprocess.
2. Open a Tauri window pointing at the configured `devUrl`
   (`http://127.0.0.1:5173` — the existing static-served
   frontend; `python -m http.server 5173` from the repo root, or
   the project's existing dev-server tooling).
3. Stop the daemon when the window closes.

## Commands the shell exposes (IPC)

The frontend can talk to the shell via `window.__TAURI__.invoke`.
Currently exposed:

- `daemon_status()` → `"daemon pid=NNNN"` or `"daemon not running"`.
  Useful for surfacing daemon-process state in the UI's status bar
  (today the bar polls `/healthz`; the Tauri command is the
  fast-path equivalent for in-shell builds).

More commands land as needed in T4-T5.

## What this directory does NOT replace

- `frontend/` — the vanilla-JS frontend served at
  `http://127.0.0.1:5173` in browser mode. The Tauri shell points
  at the same files; no fork.
- `src/forest_soul_forge/` — the daemon. Tauri spawns the
  existing daemon module; no fork.
- `start.command` / `stop.command` — the bash workflow operators
  use today. Browser mode + Tauri mode coexist for v0.5; bash
  workflow is preserved for development.

## References

- ADR-0042 §Architecture — repo structure for v0.5 onwards
- ADR-0042 §Tranche plan — T3 = this scaffolding, T4 = daemon
  binary, T5 = signed bundle + auto-updater
