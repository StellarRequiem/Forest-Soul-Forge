# ADR-0097 — The dashboard as a game-style command menu

**Status:** Accepted (2026-06-04). Shipped: the HOME hub (`frontend/js/home.js`) and
the FLEET roster (`frontend/js/fleet.js`). The sidebar fold + further polish are
roadmap (below). The framing is normative for future dashboard work.

## Context

The dashboard grew to ~18 flat tabs (Forge, Skills, Tools, Marketplace, Agents,
Approvals, Chat, Voice, Audit, Memory, Provenance, Capabilities, Security, Reality,
Orchestrator, Console, Operator). It's powerful but reads as a control panel, not a
place you *operate from* — and it buries the loop the operator actually runs.

FSF's mechanics are already game-shaped, we just didn't present them that way:

- **trust → XP / skill levels** — each unit has a level per `problem_class`, with a
  confidence interval (ADR-0095);
- **genres → classes**, traits → attributes;
- **the bounty board → the mission log** — what needs doing, ranked by uncertainty
  (ADR-0096);
- **the tiered trials → graded levels** (Baseline→L4);
- **birth → recruit a unit**; posture → alert/shield state; the audit chain → the
  run log.

The operator asked to surface this as a game-style command menu — interactive,
broken into usable parts. **Constraint (operator-standing): no roleplay / lore /
mythos.** Agents are not narrated as fictional beings. So the framing is a
functional **sci-fi ops-console** (a strategy game's command screen), where the
"game" is in the *layout + the loop + gamified data viz*, not in fiction.

## Decision

Reframe the information architecture as **a hub + a HUD + a small set of sections**,
built additively over the existing panels (no rewrite, no backend change — the hub
and roster are pure reads over the daemon API).

### 1. HOME hub (the new default landing)
An ops-console "main menu": a live **HUD** (daemon · model · units · missions [open
bounties] · trust-integrity · chain height · last activity, each with a status
light) + **section tiles** with live stats that route into the existing panels.

### 2. The sections (18 tabs → 6 usable parts)
| Section | The play of it | Folds in |
|---|---|---|
| **COMMAND** | mission board (bounties) → assign → run → watch | Console |
| **FLEET** | units as cards: class, trust-levels, status | Agents (+ Capabilities, Approvals) |
| **BUILD** | recruit units + smith tools/skills | Forge · Skills · Tools · Marketplace |
| **LOG** | the append-only run history | Audit · Provenance · Memory |
| **RULES** | posture · gates · reality checks | Security · Reality · Orchestrator · Operator |
| **COMMS** | talk to your units | Chat · Voice |

### 3. The play loop (one clear path)
`HOME → COMMAND` (a bounty surfaces: "`signal_listener` is under-tested on
`llm_think`") `→ assign` a trust-ranked unit (`FLEET`) `→ run → watch` the readout
`→ trust levels up → HOME`. A wall of tabs becomes *"check the board → send a unit →
watch it grow."*

### 4. Tone
Sci-fi ops-console: dark panels, teal/cyan glow, status dots, monospace readouts,
faint scanline. No fantasy roleplay. Gamified *data* (XP bars, mission counts, level
tiers) is visualization, not fiction.

### 5. Safety
The hub + roster are **read-only** surfaces. Destructive actions (birth, grant,
force-close, delete) stay in their existing, gated panels — consistent with
ADR-0094/0095. Presenting the fleet as a game does not lower any gate.

## What shipped

- **HOME hub** (`af630c0`) — HUD + 6 live-stat tiles; the new default landing.
- **FLEET roster** (`4d16785`) — 67+ units as cards with class chips + trust-level
  bars; veterans (with a track record) sort first; the untested read "awaiting first
  mission" (which is exactly what the bounty board targets).

## Roadmap

- **Fold the sidebar** into the six sections (so the nav matches the hub).
- **COMMAND as true mission control** — the bounty board as the active feed with the
  trust-ranked "assign a unit" flow front-and-centre (the loop on one screen).
- **Polish** — extend the ops-console skin into the panels; a posture "shield"
  indicator; retire the legacy welcome banner.

## Relationship to prior ADRs

- **ADR-0096 (operator console + task exchange).** This is its presentation layer —
  the console *becomes* COMMAND; the bounty board *becomes* the mission feed.
- **ADR-0095 (synaptic layer).** Trust → the units' XP/levels; routing → "assign";
  bounties → missions; quarantine → benched units.
- The **MCP connector** exposes the same read surface to Claude, so the operator can
  drive the fleet from the menu *or* from an MCP client.
