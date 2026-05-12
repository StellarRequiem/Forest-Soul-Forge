# Computer-Control Safety Guide

Operator-facing guide for the `soulux-computer-control` plugin
(ADR-0048) when used by the Persistent Assistant (ADR-0047).
Read this before flipping the allowance preset to anything beyond
**Restricted**.

This guide is the §0 Hippocratic-gate companion to the technical
ADR — it spells out what the surface actually does to YOUR Mac,
how to reason about each preset, and how to roll back when
something goes wrong.

---

## What the plugin does

The `soulux-computer-control` plugin gives a Forest assistant agent
six macOS automation primitives, dispatched through Forest's
existing governance pipeline (constitution, posture, grants,
audit chain).

| Tool | Side effect | Approval | What it does |
|---|---|---|---|
| `computer_screenshot.v1` | read_only | none | `screencapture -x` to `~/.forest/screenshots/`. Returns path + size. Capped at 4 MB inline base64. |
| `computer_read_clipboard.v1` | read_only | none | `pbpaste`. Returns text + length. Text only; non-text clipboard data isn't surfaced. |
| `computer_click.v1` | external | per-call | `osascript` System Events click at integer (x, y). Requires Accessibility permission. |
| `computer_type.v1` | external | per-call | `osascript` System Events keystroke into the focused app. 4000-char cap per call. Requires Accessibility permission. |
| `computer_run_app.v1` | external | per-call | `open -a "<App Name>"`. Rejects names containing `/` or null bytes. |
| `computer_launch_url.v1` | network | per-call | `open <url>`. http://, https://, or mailto: only. file:// and javascript:// refused. |

**The plugin does NOT:**

- Take photos or video — `computer_screenshot.v1` captures the
  current display, not the camera.
- Read files outside `~/.forest/screenshots/` — paths containing
  `..` or `/` in the filename are rejected before `screencapture`
  runs.
- Read the clipboard's binary contents (images, files) — only
  text via `pbpaste`.
- Launch arbitrary executables — `computer_run_app.v1` rejects
  paths; only macOS-resolvable app names work.
- Open `file://` or `javascript://` URLs — `computer_launch_url.v1`
  enforces a scheme allowlist.
- Type unbounded text in one call — 4000-char cap blocks runaway
  loops from spamming gigabytes of keystrokes.

---

## macOS permissions you will hit

Two permissions matter. Forest does NOT grant these to itself —
you grant them in **System Settings → Privacy & Security**.

### Screen Recording

Required for `computer_screenshot.v1`. macOS will prompt the first
time the daemon's parent process tries to capture; grant to
**Terminal** (or whichever process is running the Forest daemon).

If the screenshot tool returns
`screencapture_no_output`, that's the permission missing.

### Accessibility

Required for `computer_click.v1` and `computer_type.v1`. Without
it, `osascript` System Events calls fail with
`"1002: not authorized to send Apple events to System Events"`.
The plugin recognizes this error and surfaces an actionable
message pointing at System Settings → Privacy & Security →
Accessibility.

Grant to the same process that the daemon's parent runs under.
After granting, you do NOT need to restart the daemon — the next
click/type call picks up the permission.

---

## The three allowance presets

Set via the Chat tab → Assistant mode → Settings → "Computer-control
allowances" preset row.

### Restricted

**All grants revoked** — plugin-level AND any per-tool grants.
The assistant has zero access to any of the six tools, including
the read-only ones. Use this when:

- You're handing the chat over to someone else and don't want
  the assistant to see your screen
- A screen recording is in progress and you don't want
  screenshots of sensitive material accumulating in
  `~/.forest/screenshots/`
- You're done driving the assistant for the day and want a
  clean "off" state

The assistant's constitutional kit (`llm_think`, `memory_recall`,
`memory_write`, `timestamp_window`) keeps working — it just can't
touch the computer.

### Specific (default for the cautious operator)

**No plugin-level grant. Per-tool grants seeded for the two
read_only tools** (`computer_screenshot`, `computer_read_clipboard`)
at yellow tier. Operator extends via the Advanced toggle grid
(see below) to add action tools selectively.

Effect:

- The assistant can see your screen + read your clipboard
  whenever it asks (the two seeded per-tool grants).
- Every other tool stays refused unless you toggle it on in
  Advanced.

This is the recommended default for operators who want the
assistant to be useful for observation but want to extend
write access one tool at a time.

### Full

**Plugin-level grant at green tier covers all six tools** +
per-tool overrides cleared. Combined with green agent posture,
the assistant skips per-call approval for action tools (per the
ADR-0045 T3 posture × per-grant matrix — yellow agent + green
grant downgrades to ungated for THIS plugin's calls).

Use this when:

- You're confident in how the assistant handles a specific
  workflow and the per-call approvals have become friction
- The workspace is already locked down (you're in a sandbox VM
  or a non-production environment)

**Don't pick Full as a default.** Per-call approval is your
single biggest visibility surface into what the assistant
actually does.

### Per-tool granularity in Advanced (ADR-0053)

Expand the "Advanced — per-tool toggles" disclosure below the
preset row to see all six tools with individual checkboxes. The
**Per-tool grant** column shows the current coverage state:

- `(per-tool <tier>)` — an explicit per-tool grant exists. This
  override takes precedence over the plugin-level grant for THIS
  tool (specificity-wins resolution per ADR-0053 D3).
- `(via plugin-level)` — no per-tool row exists; this tool is
  covered by the plugin-level grant.
- blank — no grant covers this tool. It will be refused at
  dispatch.

**Toggle semantics:**

- **Check a row that was unchecked** → issues a per-tool grant
  at yellow tier (cautious default; per-call approval still
  fires for action tools, none for read tools).
- **Uncheck a row that had a per-tool grant** → revokes that
  per-tool grant. If the plugin-level grant also exists, the
  tool falls back to plugin-level coverage; otherwise it
  becomes refused.
- **Uncheck a row covered ONLY by the plugin-level grant** →
  issues a per-tool grant at **red** tier. This is the "carve
  out a denial inside a broad grant" pattern: the rest of the
  plugin stays open under the plugin-level grant; this one
  tool gets explicitly refused via the per-tool override.

**Canonical configurations:**

| Goal | Setup |
|---|---|
| Let the assistant SEE but not act | Specific preset (seeds the 2 read tools). Don't toggle any others. |
| Let it see + click but never type | Specific + toggle on `computer_click.v1`. Leave type/run_app/launch_url off. |
| Open it all up except merging PRs (hypothetical mirror to a GitHub plugin) | Full + uncheck the specific tool you want denied; it lands as a red per-tool override against the green plugin-level grant. |
| Reset cleanly | Restricted. Everything zeroed. |

Per-tool grants emit `agent_plugin_granted` audit events with a
non-null `tool_name` field — see the Audit-chain forensics section
below for the jq query that filters per-tool grants from
plugin-level grants.

---

## Posture as the global brake

The posture dial (green / yellow / red) sits ABOVE the allowance
preset and **dominates** it for non-read-only tools (per ADR-0048
Decision 4):

| Posture | Behavior |
|---|---|
| green | Grants decide. Read fires freely; action tools fire if granted-skip-approval, else approval-gated. |
| yellow | All non-read calls force PENDING approval, even with Full preset. The "I'm watching" mode. |
| **red** | All non-read calls REFUSED outright, even with Full preset. Read still fires. **The emergency stop.** |

**Use red as the global brake.** Mid-workflow, if the assistant
starts doing something you didn't expect, flip the posture to
red. The assistant immediately stops acting — even if it has a
queued approval, the dispatcher refuses the call. Your grant
state is preserved; flipping back to green resumes operations
without re-issuing grants.

Posture flips are audited (`agent_posture_changed` events) so
the chain captures both the brake-pull and the resume.

---

## Audit-chain forensics

After any session involving computer-control, you can reconstruct
exactly what the assistant did. Every call in the dispatcher emits
the standard event vocabulary (no new event types per ADR-0048
Decision 6):

- `tool_call_dispatched` — args, agent, session, tool_key
- `tool_call_succeeded` / `tool_call_failed` — outcome
- `tool_call_pending_approval` — when approval gates fired
- `tool_call_approved` / `tool_call_rejected` — operator decisions
- `agent_plugin_granted` / `agent_plugin_revoked` — preset changes
- `agent_posture_changed` — posture flips

Quick chain queries (assuming default chain at
`examples/audit_chain.jsonl`):

```bash
# What did the assistant do today?
jq 'select(.event_type | startswith("tool_call_") and contains("computer_"))' \
   examples/audit_chain.jsonl

# When did you flip posture?
jq 'select(.event_type == "agent_posture_changed")' \
   examples/audit_chain.jsonl

# Did anyone change allowances?
jq 'select(.event_type | startswith("agent_plugin_"))' \
   examples/audit_chain.jsonl

# Only the per-tool grant/revoke operations (ADR-0053 surface):
jq 'select(.event_type | startswith("agent_plugin_")) |
    select(.event_data.tool_name != null)' \
   examples/audit_chain.jsonl

# Only the plugin-level grant/revoke operations (ADR-0043 original):
jq 'select(.event_type | startswith("agent_plugin_")) |
    select(.event_data.tool_name == null)' \
   examples/audit_chain.jsonl
```

The chain is append-only and hash-linked (ADR-0005); tampering is
detectable. A computer-control call that the chain doesn't record
either didn't happen or your chain integrity is compromised — both
are diagnostically useful signals.

---

## Threat model: what can a malicious assistant actually do?

The assistant runs your local LLM (Ollama by default, per ADR-0047
+ companion-genre `local_only` provider constraint). The threat
isn't a malicious model — it's prompt injection. Suppose the
assistant ingests a webpage or document containing instructions
that try to subvert its constitution. With the plugin in **Full**
preset and posture **green**, what can it do?

**Bounded by the per-tool defenses:**

- `computer_run_app.v1` cannot launch an arbitrary executable
  file path — names containing `/` are refused at the server
  level, before `open -a` runs.
- `computer_launch_url.v1` cannot open `file://` or
  `javascript://` URLs — scheme allowlist.
- `computer_type.v1` is capped at 4000 chars per call — no
  gigabyte spam loop in one shot.
- `computer_click.v1` requires integer coords — no string-
  injection into the osascript body.
- All four action tools surface `requires_human_approval=true`
  in the manifest. The Full preset (green tier plugin-level
  grant) combined with green agent posture is what unlocks
  granted-skip behavior per the ADR-0045 T3 posture × per-grant
  matrix; without both green signals, per-call approval fires.
  Per ADR-0053 D3 the per-tool resolver applies the same matrix
  using the per-tool grant's tier when one exists, so a yellow
  per-tool grant on a green plugin-level grant still gates THAT
  specific tool while leaving the others ungated.

**Bounded by posture:**

- Flipping to red refuses every non-read call. A prompt-injected
  assistant cannot un-red itself — posture is operator-only;
  there's no `set_posture` tool exposed to the agent.

**Bounded by the audit chain:**

- Every action lands in the chain. Even if you miss something
  in real-time, you can reconstruct what happened afterward and
  identify the prompt-injection vector for the next session.

**Things outside the threat model the plugin can NOT defend:**

- A constitution that explicitly grants action tools without
  approval — that's an operator misconfiguration, not a plugin
  failure. The constitution is operator-authored.
- macOS-level vulnerabilities (osascript / screencapture /
  open exploits). The plugin trusts the system binaries.
- An operator who approves a clearly malicious approval prompt.
  The Approvals tab shows args before you approve — read them.

---

## Common scenarios

### "I want the assistant to read my screen but never click anything."

Posture: green. Allowance preset: **Specific**. Then in practice
just don't approve any click/type/run_app/launch_url prompts —
they'll queue but never execute.

If you want stronger enforcement, posture: yellow forces PENDING
on every action call regardless. Less ergonomic (more prompts) but
"never accidentally approve" hardens the surface.

### "I'm about to run a sensitive command and don't want the assistant clicking around mid-flight."

Posture: red. Allowance preset: doesn't matter. Your action
window is locked. Resume by flipping posture back to green or
yellow when done.

### "The assistant clicked somewhere weird. What did it just do?"

1. `tail -1 examples/audit_chain.jsonl | jq .` — most recent event.
2. If the most recent event is a `tool_call_succeeded` for
   `computer_click`, the args show the (x, y) it clicked at.
3. If the chain shows a long tool sequence you didn't expect,
   flip posture to red (emergency stop) and walk back through
   the events.

### "I want to clean up old screenshots."

`~/.forest/screenshots/` is operator-managed; Forest doesn't auto-
clean it. Periodically:

```bash
find ~/.forest/screenshots -mtime +30 -delete
```

Or set up a launchd job. The audit chain references the path but
doesn't depend on the file existing for chain integrity.

### "I want to revoke this assistant entirely."

1. Reset the binding from the Chat tab → "reset assistant binding"
   button. The assistant's instance_id is forgotten by the
   frontend; the agent itself stays in the registry (audit chain
   integrity).
2. Optionally archive the agent from the Agents tab. That marks
   it status=archived but keeps the constitution + history for
   forensics.
3. Posture state, grants, and memory consents stay tied to the
   agent's instance_id. Re-binding to the same agent restores
   them; binding a NEW agent (per ADR-0001 identity model: a
   fresh DNA + constitution_hash) starts from defaults.

---

## Quick reference card

```
Default for cautious operators:
  posture = green, preset = Specific
  → see screen freely; approve every action

Hardened for sensitive workspace:
  posture = yellow or red, preset = Restricted or Specific

Trusted workflow you've validated:
  posture = green, preset = Full  (forward-compat; today same as Specific)

Emergency stop:
  posture = red  (revokes nothing; freezes action surface immediately)

Visibility:
  Audit tab in the frontend, OR jq examples/audit_chain.jsonl

Permissions (System Settings → Privacy & Security):
  Screen Recording → screencapture
  Accessibility   → click + type
```

---

## References

- ADR-0047 — Persistent Assistant Chat (operator's chat surface;
  the assistant agent that uses this plugin lives here)
- ADR-0048 — Computer Control Allowance (this plugin's design)
- ADR-0045 — Agent posture (the global brake; integration in
  Decision 4)
- ADR-0043 — Plugin protocol + grants (the substrate the
  allowance presets ride)
- ADR-0019 — Tool dispatch + governance pipeline (where every
  call gets gated)
- ADR-0005 — Audit chain (append-only, hash-linked record)
