# SoulUX Computer Control

macOS automation plugin for the Forest-Soul-Forge Persistent Assistant
(per [ADR-0047](../../../docs/decisions/ADR-0047-persistent-assistant-chat.md)
+ [ADR-0048](../../../docs/decisions/ADR-0048-computer-control-allowance.md)).

Gives a Forest assistant the same surface as ChatGPT-Operator /
Claude-Computer-Use — see the screen, click, type, launch apps, open
URLs, read clipboard — but bound by Forest's existing governance
discipline:

- **Constitution** controls per-agent which capabilities are even
  visible (`allowed_mcp_servers` + `allowlisted_tools`)
- **ADR-0045 posture** clamps action tools at runtime: green / yellow /
  red. Red means refused; yellow means per-call approval; green means
  grants decide
- **ADR-0019 governance pipeline** runs every dispatch through the
  same 8 pre-execute checks every other tool sees
- **ADR-0043 grants** issue runtime per-(agent, plugin) capability
  augmentations without rebirthing the agent
- **Audit chain** captures every dispatch + approval / refusal /
  posture-change so the operator can reconstruct what happened

## Status: T3 action tools shipped (B164) — full v1 six-tool surface live

The plugin's v1 surface is complete:

| Tool | side_effects | requires_human_approval | Wraps |
|---|---|---|---|
| `computer_screenshot.v1` | read_only | false | `screencapture -x -t png` |
| `computer_read_clipboard.v1` | read_only | false | `pbpaste` |
| `computer_click.v1` | external | true | `osascript` System Events click |
| `computer_type.v1` | external | true | `osascript` System Events keystroke |
| `computer_run_app.v1` | external | true | `open -a` |
| `computer_launch_url.v1` | network | true | `open <url>` |

The server is a single-file stdlib-only Python script handling
JSON-RPC stdio per Forest's `mcp_call.v1` wire protocol. macOS
Accessibility permission required for click + type to function;
the server surfaces a clear actionable error when permission is
missing.

T4 (Allowance UI in the Chat-tab settings panel) is the remaining
ADR-0048 deliverable that completes ADR-0047 T4 — the operator
gets the three-preset toggle (Restricted / Specific / Full) plus
Advanced disclosure for per-tool overrides.

## Tranche roadmap

Per [ADR-0048 §Implementation tranches](../../../docs/decisions/ADR-0048-computer-control-allowance.md#implementation-tranches):

| # | Tranche | Status | Tools added |
|---|---|---|---|
| T1 | Scaffold | **DONE (B159)** | — |
| T2 | Read tools | **DONE (B163)** | `computer_screenshot.v1`, `computer_read_clipboard.v1` |
| T3 | Action tools | **DONE (B164)** | `computer_click.v1`, `computer_type.v1`, `computer_run_app.v1`, `computer_launch_url.v1` |
| T4 | Allowance UI | pending | (frontend) Chat-tab three-preset settings panel + Advanced disclosure |
| T5 | Posture clamp logic | **DONE (B160)** | docs + 12 ADR-0048 coverage tests in PostureGateStep |
| T6 | Documentation + safety | pending | per-tool docs + `docs/runbooks/` operator safety guide |

## Why this isn't bundled in the kernel

ADR-0044 (kernel positioning) names the seven v1.0 ABI surfaces. The
plugin protocol (substrate this plugin sits on) is one of them; the
specific tools this plugin offers are NOT. Operators who don't want
computer-control don't install the plugin; the kernel itself stays
unchanged. Per ADR-0048 Decision 1 — userspace-only delivery.

## Why six tools (T2 + T3) initially

The smallest set that covers "see + click + type + launch":

- **see**: `computer_screenshot` + `computer_read_clipboard`
- **act**: `computer_click` + `computer_type`
- **launch**: `computer_run_app` + `computer_launch_url`

Future tools (`computer_drag`, `computer_key`, `computer_window_*`,
`computer_double_click`, `computer_right_click`, `computer_scroll`,
`computer_read_pixel`) ship in subsequent plugin minor versions
once the v1.0 six-tool surface stabilizes. No need to ship them all
in the first release; each carries its own audit + governance burden.

## Per-tool side-effect classification

These ride the existing dispatcher classification (per ADR-0019):

| Tool | side_effects | requires_human_approval |
|---|---|---|
| `computer_screenshot.v1` | read_only | false |
| `computer_read_clipboard.v1` | read_only | false |
| `computer_click.v1` | external | true |
| `computer_type.v1` | external | true |
| `computer_run_app.v1` | external | true |
| `computer_launch_url.v1` | network | true |

The two read-only tools mean the assistant can "see what's on screen"
without ANY action surface — that alone is a useful capability gradient.

## Companion-genre kit-tier ceiling

The Persistent Assistant uses the `companion` genre (per ADR-0047 T6).
Companion's `max_side_effects` is `network` — but the genre policy
also caps the assistant's STANDARD kit at `read_only` (constitutional
floor). External tools enter the assistant's effective kit ONLY via
explicit per-(agent, plugin) grants on this plugin. That is by design:

- Default state: assistant has llm_think + memory_recall + memory_write
  + timestamp_window. No clicking, no typing.
- After grant: assistant gains the granted tools; each call still
  flows through `requires_human_approval` (Decision 2) and posture
  clamps (Decision 4).

The grant is an explicit operator action through the Chat-tab settings
panel (T4 / ADR-0048 UI). It is revocable at runtime without
rebirthing the agent.

## Install

The repo ships a turnkey installer:

```bash
~/Forest-Soul-Forge/dev-tools/install-soulux-computer-control.command
```

It picks the daemon's venv Python (so dependencies resolve),
runs `plugin install` against `~/.forest/plugins`, hits
`POST /plugins/reload` to pick up the new server, and verifies
the daemon's active list includes the entry. Output goes to
`/tmp/fsf-plugin-install.log`.

If you want to run the steps by hand:

```bash
cd ~/Forest-Soul-Forge

# Use the daemon's venv Python — the system python3 typically
# doesn't have the runtime deps (pydantic, etc.) installed.
.venv/bin/python -m forest_soul_forge.cli.main \
    plugin install ./examples/plugins/soulux-computer-control \
    --plugin-root ~/.forest/plugins

# Reload the daemon's plugin runtime so it picks up the new server.
TOKEN=$(grep ^FSF_API_TOKEN= .env | cut -d= -f2)
curl -X POST http://127.0.0.1:7423/plugins/reload \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: install-$(date +%s)"
```

The `fsf` console script registered in `pyproject.toml` only ends
up on PATH when the package was `pip install -e .`'d. Operators
running just the daemon's venv don't have it; using
`.venv/bin/python -m forest_soul_forge.cli.main` is the same code
path with no PATH dependency. (Surfaced live during the 2026-05-06
e2e test: B176 added the wrapper script + this note.)

After install:

- The plugin appears in the daemon's active list (`GET /plugins`).
- Issue grants per agent via the Chat-tab Assistant settings
  panel's "Computer-control allowances" preset row (Restricted /
  Specific / Full) — see B165 / B175.
- Or via the `POST /agents/{instance_id}/plugin-grants` HTTP
  surface directly.

## Audit + reverse engineering

Every computer-control call lands in the audit chain via the standard
dispatcher events: `tool_call_dispatched`, `tool_call_succeeded` /
`_failed` / `_pending_approval` / `_approved` / `_rejected`,
`agent_plugin_granted` / `_revoked`, `agent_posture_changed`. No
new event types added (per ADR-0048 Decision 6). Reconstruct any
session by `grep`-ing the audit chain for the agent's instance_id.
