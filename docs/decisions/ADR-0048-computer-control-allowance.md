# ADR-0048 — Computer Control Allowance for Assistant

**Status:** Proposed (2026-05-05). Pairs with ADR-0047 (Persistent
Assistant Chat). Userspace-only delivery — uses existing kernel ABI
surfaces (ADR-0043 plugin protocol, ADR-0045 posture, ADR-0019 tool
dispatch + governance pipeline) without modifying any of them.

## Context

ADR-0047 ships a Persistent Assistant agent — one operator, one
ongoing conversation, persistent context across sessions. To be
genuinely useful (vs. just a conversational front-end on an LLM),
the assistant needs to **act**: take screenshots, click around, type,
launch apps, run shell commands, edit files.

Forest already ships some action tools:

- `shell_exec.v1` — argv-list-only shell exec, gated by
  `allowed_commands` allowlist + `allowed_paths` cwd constraint
- `code_read.v1` — file read with `allowed_paths`
- `code_edit.v1` — file write (atomic temp+rename) with
  `allowed_paths`
- `web_fetch.v1` — HTTP GET with allowlisted hosts
- `browser_action.v1` — headed browser actions per ADR-003X

What's missing from the macOS-automation surface:

- Screen capture (read what's on the operator's screen)
- Clipboard access (read/write)
- Mouse click / drag (accessibility-driven UI control)
- Keyboard input (typed into focused app)
- App launching / quitting (osascript / `open -a`)
- Window management (focus, hide, resize)

These are the things ChatGPT-Operator / Claude-Computer-Use surface,
plus what Cowork itself uses to drive the operator's Mac. Forest's
assistant should have the SAME capability gradient — but bound by
Forest's existing governance discipline (constitution, posture,
grants, audit, approval queue).

The substrate for trust-bounded action is fully built:

- **ADR-0045 (posture)** — runtime trust dial: green / yellow / red,
  per-agent, mutable, every change audited
- **ADR-0043 (plugin protocol + grants)** — per-(agent, plugin)
  grants augment the constitution at runtime without rebirthing the
  agent; sha256-pinned entry points; per-tool
  `requires_human_approval` map
- **ADR-0019 (tool dispatch)** — every dispatch flows through the
  governance pipeline (constitution constraints, genre kit-tier
  ceiling, initiative ladder, per-session counters, approval gates,
  posture gate); refusals + successes + failures all in audit chain
- **ADR-0027 (memory privacy contract)** — actions that touch
  cross-agent memory require explicit consent

What's missing: a **unified macOS computer-control tool surface** +
a **UI to grant/revoke at runtime** + **per-category ergonomics** so
the operator can think "let it click" instead of "grant
soulux-computer-control:computer_click.v1".

This ADR specifies that surface as a SoulUX MCP plugin.

## Decision

Ship a **SoulUX MCP plugin** at
`examples/plugins/soulux-computer-control/` providing macOS computer-
control tools, dispatched through Forest's existing governance
pipeline. The Persistent Assistant Chat tab (ADR-0047) exposes a
**hybrid allowance UI** — friendly category toggles backed by
precise per-tool grants — for runtime configuration.

### Decision 1 — Userspace-only delivery via MCP plugin

The plugin lives in `examples/plugins/soulux-computer-control/`
following the established plugin protocol (ADR-0043). Like the other
example plugins (`forest-echo`, `brave-search`, `filesystem-reference`)
it:

- Has a `plugin.yaml` manifest with sha256-pinned entry point
- Declares per-tool `side_effects` and `requires_human_approval`
- Loads via the existing plugin loader (`POST /plugins/install`)
- Can be enabled/disabled per agent via the existing grant surface
  (ADR-0043 follow-up #2 substrate)

**ZERO changes to kernel ABI.** Kernel surfaces unchanged:

- Tool dispatch protocol — plugin's tools dispatch through
  `mcp_call.v1` like every other plugin
- Audit chain schema — uses existing `tool_call_dispatched` /
  `_succeeded` / `_failed` events; per-tool args + results land in
  the standard event_data shape
- Plugin manifest schema v1 — used as-shipped
- Constitution.yaml schema — `allowed_mcp_servers` already supports
  this; per-tool constraints already supported
- HTTP API contract — uses existing `/agents/{id}/plugin-grants`
  endpoints (ADR-0043 follow-up #2)
- CLI surface — uses existing `fsf plugin` subcommands

### Decision 2 — Tool surface (initial 6 tools)

The plugin ships with six tools chosen to cover the minimum-viable
"assistant can drive my Mac" surface:

| Tool | side_effects | requires_human_approval | What |
|---|---|---|---|
| `computer_screenshot.v1` | read_only | false | Capture the visible screen; return PNG bytes (or path) |
| `computer_read_clipboard.v1` | read_only | false | Read current clipboard contents (text only initially) |
| `computer_click.v1` | external | true | Left-click at (x, y) on the screen |
| `computer_type.v1` | external | true | Type a string into the focused app |
| `computer_run_app.v1` | external | true | Launch an app by name (`open -a "App Name"`) |
| `computer_launch_url.v1` | network | true | Open a URL in the default browser |

**Why these six initially:**

- Screenshot + clipboard-read are **read-only** — operators can grant
  permissively. The assistant can "see what I'm looking at" without
  any action surface.
- Click, type, run_app, launch_url are **external** — the four
  primitives that compose into nearly any macOS automation. Each
  approval-gated by default; operator can grant per-tool to bypass
  approval per call (ADR-0043 follow-up #1 substrate).
- Each tool has a constitution-policy per the dispatcher's existing
  governance pipeline. Genre kit-tier ceiling applies — companion
  genre's `read_only` risk floor means the assistant CAN'T fire
  `external` tools without a per-(agent, plugin) grant that augments
  its constitution. Posture clamps further per Decision 4.

**Future tools (post-v1.0 of the plugin):**

- `computer_drag.v1` — left-click-drag for window moves, drawing,
  selection
- `computer_hold_key.v1` / `computer_key.v1` — shift, cmd, modifier
  combos
- `computer_window_focus.v1` / `_resize.v1` / `_hide.v1` — window
  management
- `computer_double_click.v1` / `_right_click.v1` — additional click
  primitives
- `computer_scroll.v1` — wheel/trackpad scroll
- `computer_read_pixel.v1` — read a single pixel's color (cheap
  alternative to full screenshot for game-bot-style use)

These ship in subsequent plugin tranches; v1.0 of the plugin is the
six above.

### Decision 3 — Three-preset allowance UI + Advanced disclosure (operator framing 2026-05-06)

The Chat-tab settings panel (per ADR-0047 Decision 5) presents the
allowances as **three preset tiers** the operator picks from, plus
an Advanced disclosure for fine-grained per-tool / trust-tier
control. Each preset is a named configuration that maps to a
specific set of per-(agent, plugin) grants under the hood.

**Preset tiers:**

| Preset | Grants issued | Operator intent |
|---|---|---|
| **Restricted** | `computer_screenshot.v1`, `computer_read_clipboard.v1` (read_only) | "Let the assistant SEE what's on my screen, but never act. Approval-gated everything else." |
| **Specific** | Operator-picked per-category toggles (the previous Decision 3 hybrid lives here) | "I'll decide per-category which capabilities are on." |
| **Full** | All 6 tools (screenshot + clipboard + click + type + run_app + launch_url) | "Let the assistant drive my Mac. Per-call approval still fires per tool's `requires_human_approval` map; posture (yellow/red) still clamps." |

**Specific preset categories** (the per-category toggles inside
the Specific tier — preserved from the original hybrid framing):

- ☐ **Read screen** (screenshot + read clipboard) — read_only
- ☐ **Click + type** (click + type tools) — external, approval per
  call by default
- ☐ **Launch apps + URLs** (run_app + launch_url) — external /
  network, approval per call by default
- ☐ **(Future categories appear as plugin tools expand)**

**Advanced disclosure** (collapsed by default, expandable):

- Per-tool grant toggles independent of category presets — power
  users who want "click but not type" check exactly the boxes
  they want
- Per-tool trust-tier override — `computer_run_app` with an
  `allowed_apps` allowlist, `computer_launch_url` with a domain
  allowlist, etc.
- Each grant ties to a trust tier (default `standard`). Stricter-
  than-standard tiers configure here.

Each preset toggle / category toggle / per-tool toggle maps to
**per-(agent, plugin) grants** under the hood (ADR-0043 substrate).
Switching presets re-issues the appropriate grant set in one
audited transaction (`agent_plugin_granted` per tool entering
scope; `agent_plugin_revoked` per tool exiting scope).

**Why three presets + Advanced (2026-05-06 operator framing):**

- Pure per-tool grant UI = operator has to know that
  `soulux-computer-control:computer_click.v1` is a thing. Friction.
- Pure category-only UI = operator can't grant "click but not type"
  if they want fine-grained control. Limits power users.
- Three-preset UI = the most common operator postures ("just see",
  "case-by-case", "let it drive") become single-click choices.
  Specific + Advanced cover everyone whose use case doesn't match
  a preset. Recoverable: switching presets is one transaction with
  a clear audit-chain trail.

**Posture interaction (ADR-0048 Decision 4):**

Presets define what the agent COULD do; posture (green/yellow/red)
defines whether it CAN do it right now. Full preset + red posture =
all action tools refused (read tools still fire). Operators flip
posture to red as a "global brake" without losing their grant
state — switching back to green resumes the preset's grants.

**Trust tier integration (ADR-0043 #2 substrate):**

Each grant is tied to a trust tier (default `standard`). Operators
who want a stricter-than-standard tier for a specific tool (e.g.,
`computer_run_app` with allowed_apps allowlist) configure it in the
Advanced disclosure.

### Decision 4 — Posture clamps (red dominates grants)

Per ADR-0045 the agent's posture is a global trust dial: green /
yellow / red. ADR-0048 adds posture clamps for computer-control:

| Posture | Behavior |
|---|---|
| **green** | Grants decide. Read-only tools fire freely; granted external tools fire freely (no per-call approval); ungranted external tools require per-call approval. |
| **yellow** | All non-read tools require per-call approval, even if granted. Read-only tools fire freely. |
| **red** | All non-read tools refused outright, even if granted. Read-only tools fire freely. |

Posture flips take effect immediately (ADR-0045 substrate). The
Chat-tab settings panel's posture dial is the operator's "global
brake" — flip to red to stop the assistant from clicking/typing
during a sensitive operation, then flip back when ready.

**Why red dominates grants:** safety surface. The grant is "I trust
the assistant with this tool"; posture is "I want the assistant to
chill right now regardless." The latter wins; otherwise an operator
who urgently flips to red expects the assistant to STOP, not
"continue with grants."

### Decision 5 — Per-call approval flow

For tools requiring approval (per `requires_human_approval` in
plugin.yaml OR per posture clamp), the dispatcher surfaces the call
to the existing **approval queue** (ADR-0019 T4 + the Approvals tab
in the frontend). The approval ticket carries:

- The tool key + version
- The args (visible to the operator before approval)
- The agent that requested it
- A "context" field (the assistant adds: "you asked me to <X>;
  this click would be at (x, y)")

The operator approves or rejects from the Approvals tab OR from a
floating prompt in the Chat tab itself (low-friction approval for
in-conversation actions). Approved → dispatch resumes; rejected →
the assistant gets a `DispatchRefused` and reports back to the
operator.

This is the existing approval queue substrate — no new surface.

### Decision 6 — Audit-chain visibility

Every computer-control tool call lands in the audit chain via the
standard dispatcher events:

- `tool_call_dispatched` — args, agent, session, tool_key
- `tool_call_succeeded` — output, tokens_used (0 for non-LLM), result_digest
- `tool_call_failed` — exception_type, audit_seq pointer
- `tool_call_pending_approval` — when approval gates fire
- `tool_call_approved` / `tool_call_rejected` — operator decisions
- `agent_plugin_granted` / `_revoked` — when category toggles change
- `agent_posture_changed` — when posture flips

This is the existing event vocabulary — no new event types added.
Operator can query the chain (or the Audit tab) to reconstruct
exactly what the assistant did, when, with what args, and what the
operator approved.

## Implementation tranches

| # | Tranche | Description | Effort |
|---|---|---|---|
| T1 | Plugin scaffold | **DONE B159** — `examples/plugins/soulux-computer-control/` directory + plugin.yaml + entry point + README. No tools yet — empty MCP server (capabilities: []). | 0.5 burst |
| T2 | Read tools | **DONE B163** — `computer_screenshot.v1` (wraps `screencapture -x -t png`, writes to `~/.forest/screenshots/`, returns path + size + format) and `computer_read_clipboard.v1` (wraps `pbpaste`, returns text + length). side_effects=read_only, no approval. JSON-RPC stdio server (single-file, stdlib-only — no external SDK to keep sha256 stable). 9 unit tests cover wire protocol, error paths, defense-in-depth filename rejection. macOS-only; non-Darwin returns clean platform_unsupported error. | shipped |
| T3 | Action tools | **DONE B164** — `computer_click.v1` + `computer_type.v1` (osascript System Events; needs Accessibility permission), `computer_run_app.v1` (`open -a`), `computer_launch_url.v1` (`open <url>` with http/https/mailto allowlist; file:// + javascript:// refused). All four side_effects=external (launch_url=network) and `requires_human_approval=true` per Decision 2. Defense-in-depth: integer-coord type check on click; 4000-char cap on type; path-separator + null-byte rejection on run_app; URL-scheme allowlist on launch_url. macOS-only (non-Darwin → platform_unsupported). | shipped |
| T4 | Allowance UI | **DONE B165 (partial)** — Chat-tab assistant settings panel ships three preset buttons (Restricted / Specific / Full) wired to POST/DELETE `/agents/{id}/plugin-grants`. Restricted revokes; Specific grants at `standard` tier; Full grants at `elevated` tier. Live grant-state indicator + per-tool reference table inside Advanced disclosure. **Per-tool granularity in Advanced awaits substrate extension** — Forest's plugin-grants API is plugin-scoped today; per-tool grant rows would need a schema migration. ADR-0045 T3's per-grant-tier substrate already differentiates `standard` vs `elevated` at the dispatcher level, so the trust_tier choice is forward-compatible. Closes ADR-0047 T4 fully (the allowances stub is gone). | shipped |
| T5 | Posture clamp logic | **DONE B160** — ADR-0045's existing PostureGateStep already implements Decision 4 via its side_effects-based logic (B114 + B115). T5 reduced to a doc + test-coverage pass: docstring updated to cite ADR-0048 + 12 new tests confirm coverage of computer_screenshot/clipboard/click/type/run_app/launch_url across green/yellow/red. No new gate code needed — substrate "just works" because it operates on side_effects, not tool name. | shipped |
| T6 | Documentation + safety | **DONE B166** — Plugin README in place since T1 (B159), updated each tranche to track shipped surface. Operator-facing safety guide at `docs/runbooks/computer-control-safety.md` covers: what the plugin does + doesn't, three preset semantics, macOS permission requirements (Accessibility for click/type, Screen Recording for screenshot), audit-chain forensics with jq examples, posture as emergency stop, threat model (prompt injection vs. defense-in-depth), common scenarios, quick-reference card. | shipped |

Total estimate: 5-6 bursts. Can ship in pieces — T1+T2 (read-only)
provides immediate value (assistant can see the screen); T3 unlocks
action; T4 polishes UX; T5 closes the safety surface; T6 documents.

## Consequences

**Positive:**

- Forest's assistant gets feature parity with ChatGPT-Operator /
  Claude-Computer-Use, but with Forest's audit + governance + posture
  layered in
- Strictly additive: existing tools (shell_exec, code_read, etc.)
  unchanged; this is new computer-control surface for a different
  use case
- ZERO kernel ABI impact — uses existing plugin protocol, posture,
  grants, dispatch governance
- Operator gets a friendly "let it click" UI without losing per-tool
  precision
- Sets up the "another outside analysis" arc with a concrete
  capability surface to evaluate

**Negative:**

- Adds a substantial security surface. macOS automation tools can do
  damage if mis-granted. Mitigations: posture-clamps (Decision 4),
  approval-gated by default (Decision 2), per-(agent, plugin) grants
  (Decision 3), audit-chain visibility (Decision 6), trust tiers
  (Decision 3). Defense-in-depth.
- The plugin depends on macOS-specific automation (osascript,
  screencapture, accessibility APIs). Cross-platform operators
  (Linux, Windows) need a different plugin. Acceptable — Forest is
  cross-platform at the kernel level; the SoulUX plugin can be
  macOS-only.
- The plugin needs accessibility permissions on macOS (System Settings
  → Privacy & Security → Accessibility). Document in T6.
- Six tools is a starting point; a full computer-control vocabulary
  is bigger. Operators will ask for `computer_drag`, `computer_scroll`,
  etc. immediately. Plan for the v1.0 → v1.1 expansion path.

**Neutral:**

- Cowork uses similar primitives (computer-use MCP server). This ADR
  doesn't reuse Cowork's implementation — Forest's plugin is
  independent — but the design choices (categorical UI, per-call
  approval, posture clamps) parallel Cowork's safety model.
- The plugin doesn't bundle its own LLM-vision or screenshot-OCR.
  The assistant can take screenshots, but interpreting them is up
  to the LLM (qwen2.5-coder:7b doesn't have vision). Future tranches
  could add vision-LLM routing if the operator's chosen model
  supports it.
- The "computer control" name is operator-facing. The kernel-level
  name is just "an MCP plugin with macOS-automation tools." Keep the
  vocabulary clean.

## What this ADR does NOT do

- **Does not add a new kernel ABI surface.** Plugin protocol,
  posture, grants, dispatch governance — all unchanged.
- **Does not specify the plugin's LLM-vision capabilities.** The
  assistant takes screenshots; interpreting them requires a
  vision-capable LLM (operator's call).
- **Does not deprecate `shell_exec.v1`, `code_read.v1`,
  `code_edit.v1`, `web_fetch.v1`, `browser_action.v1`.** Those
  remain available for non-assistant use cases (security swarm,
  SW-track, scheduled tasks). The computer-control plugin is
  additive.
- **Does not bundle Linux or Windows variants.** SoulUX is
  macOS-first; cross-platform plugins are a future contributor
  opportunity.
- **Does not specify a payment / subscription model for the plugin.**
  Apache 2.0, free, ships in `examples/plugins/`. No license
  surface differs from the rest of Forest.
- **Does not pre-grant any tool to the assistant by default.** First
  birth = posture green, zero grants. Operator opts in by toggling
  categories in the Chat tab settings.

## References

- ADR-0019 — Tool execution runtime (the dispatcher this plugin
  plugs into)
- ADR-0027 — Memory privacy contract (cross-agent disclosure
  semantics if the assistant shares its work)
- ADR-0033 — Security Swarm (a multi-tier security model that
  parallels this ADR's posture-and-grants approach)
- ADR-0038 — Companion harm model (the assistant inherits the
  refusal scaffolding for crisis topics)
- ADR-0042 — v0.5 product direction (the SoulUX flagship surface
  this plugin lives in)
- ADR-0043 — MCP-first plugin protocol + grants (the substrate this
  ADR composes)
- ADR-0044 — Kernel positioning + SoulUX flagship (the kernel/
  userspace boundary this ADR respects)
- ADR-0045 — Agent posture / trust-light system (the runtime trust
  dial this ADR's clamps build on)
- ADR-0047 — Persistent Assistant Chat (the consumer)

## Credit

The "customizable allowance for little or full control over computer
functions" framing came from the operator (Alex) in the 2026-05-05
Cowork session as part of the chat redesign ask. The hybrid-UI
recommendation (categories backed by per-tool grants) came out of the
plan-before-act discussion in the same session. The userspace-only
framing matches the kernel/userspace boundary discipline from
ADR-0044.
