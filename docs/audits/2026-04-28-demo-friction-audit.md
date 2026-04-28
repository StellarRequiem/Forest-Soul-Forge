# Demo friction audit — 2026-04-28

Phase F2 of the Demo Edition track. Drove every tab, captured every primary
flow against the live daemon (commit `01ed81c`, port 7423). Triage is by
demo impact: **P0 = demo killer** (audience sees broken UI), **P1 = visible
polish gap** (audience notices but flow continues), **P2 = nice-to-have**
(only matters under sustained inspection).

- **Who ran it:** Claude (Cowork mode) + Alex
- **Method:** Chrome MCP driving the live frontend at 127.0.0.1:5173;
  screenshots + accessibility-tree reads per tab; cross-checked against
  the source HTML + JS modules
- **Stack state:** daemon on `data/audit_chain.jsonl` (5 lifespan events
  only — fresh process). The 500-event canonical chain is in
  `examples/audit_chain.jsonl` (the prior session's evidence).

---

## P0 — demo killers (must fix before showing anyone)

### 1. Frontend boot drops Skills + Tools panels silently

**File:** `frontend/js/app.js` lines 60-93
**Symptom:** click Skills tab → "Loading skills…" forever. Click Tools tab → "Loading tools…" forever. Both tabs render but never receive data.
**Root cause:** the `boot()` happy path (after `traitsPanel.start()` succeeds) calls `agentsPanel.start()`, `auditPanel.start()`, `pendingPanel.start()`, `memoryPanel.start()` — but **omits `skillsPanel.start()` and `toolRegistryPanel.start()`**. Both modules are imported (lines 16-17) but never invoked.
**Ironic detail:** the *error-fallback* branch (when trait tree fails) DOES call all 6 — so the broken state only surfaces when everything else works.
**Fix effort:** 2 lines, 30 seconds. Add the two missing `start()` calls.

### 2. Browser asset cache holds stale HTML between visits

**Symptom:** until I forced `cmd+shift+r`, the live page rendered only **3 of 7 tabs** (Forge, Agents, Audit). Skills/Tools/Memory/Approvals tabs were absent from the DOM despite being in the source. After hard reload they all appeared.
**Root cause:** `frontend/index.html` and the JS/CSS assets ship without cache-busting query strings. Any returning visitor hits stale assets after a deploy.
**Demo impact:** any evaluator who's ever loaded an older version of the page sees a UI missing half its features. They have no way to know `cmd+shift+r` would fix it.
**Fix:** version-stamp asset URLs (e.g., `<script src="js/app.js?v=20260428">`) or set cache-control headers on the static-serve layer. Single-file edit + nginx.conf line.

### 3. Agent detail panel 404s on every click

**File:** `frontend/js/agents.js` (uses `${role}_${dna}` as the key) vs daemon's `/agents/{instance_id}` endpoint
**Symptom:** click any agent in the Agents list → red error: **"Failed to load agent: 404 unknown agent: network_watcher_5937afd40a51"**. The list endpoint returns agents that the detail endpoint can't find.
**Root cause:** schema drift. The list endpoint returns enough info to render a card; the detail endpoint expects an `instance_id` that the card doesn't carry. The frontend reconstructs `${role}_${dna}` and posts it as if it were the instance_id — but the registry's actual instance_ids look like `log_lurker_98c6841d68e5`, not `network_watcher_5937afd40a51`.
**Demo impact:** "look at this agent we just forged" → red error. Demo over.
**Fix:** either (a) include `instance_id` in the list response payload and have the frontend use it directly, or (b) add a `/agents/by-dna/{dna}` lookup endpoint. (a) is cleaner.

### 4. Provider status shows "local · unreachable" by default

**Symptom:** top-right of every screen shows a red dot with "local · unreachable" and a "switch" button.
**Root cause:** daemon defaults to Ollama at `127.0.0.1:11434`. If Ollama isn't running (most demo machines won't have it), the dot stays red. The dot is a heartbeat — every screen, all the time.
**Demo impact:** first thing the audience sees is a red error indicator. Even if the demo works perfectly with a fallback or without LLM enrichment, the dot signals "this is broken."
**Fix:** three options ranked by effort —
   - **(easiest)** when no provider is configured/reachable, show a neutral "no provider" badge instead of red error
   - **(medium)** ship Ollama install instructions in the start script; offer a "skip LLM enrichment" demo mode that hides the indicator entirely
   - **(best)** offer a frontier provider (OpenAI-compat) configured at install time with a single env var; falls back gracefully if no key

### 5. Audit tab shows lifespan events only — runtime activity invisible

**Symptom:** Audit tab shows 3 `agent_created` events. The 47-event canonical chain from the prior swarm-bringup is in `examples/audit_chain.jsonl` but not visible because the daemon reads `data/audit_chain.jsonl`.
**Root cause:** demo data dir vs production data dir aren't separated. The `data/` dir is whatever's accumulated since the user first ran the daemon. For a demo, we need a populated chain.
**Demo impact:** the headline architectural feature — the audit chain — looks empty. Inspector tab shows 3 lines and goes silent.
**Fix:** Phase F4/F7 territory — pre-seeded demo data dir with rich audit history. For the immediate demo, point the daemon at `examples/audit_chain.jsonl` via `FSF_AUDIT_CHAIN_PATH` env var (already supported per `daemon/config.py`).

### 6. The 9 swarm agents from prior sessions aren't in the current registry

**Symptom:** Agents tab shows 3 `VoiceTest` agents, all archived. None of the LogLurker / AnomalyAce / ResponseRogue / VaultWarden agents from the audit doc.
**Root cause:** same data-dir issue as #5. The swarm was birthed against a different daemon state.
**Demo impact:** "the security swarm" is a featured story; if the agents aren't in the registry, the story doesn't exist on-screen.
**Fix:** ship a demo data dir with the 9 agents pre-birthed. Or: a one-button "seed demo state" that runs the swarm-bringup against a clean registry.

---

## P1 — visible polish gaps (audience notices but flow continues)

### 7. No welcome / value prop / first-action CTA above the fold

The user lands on PROFILE → SECURITY → DEFENSIVE_POSTURE without ever seeing what the product *is* or what they should do first. The "Forest Soul Forge — local-first agent foundry" tagline is the only context, and it's small in the top bar. **Fix:** banner above the trait sliders on first load: *"Drag sliders to shape an agent's personality. Click Birth to give them a soul. Then watch them work in the other tabs."* Dismissible. Phase F3.

### 8. "PREVIEW idle" status string is opaque

When you start changing sliders, the status flips between "idle" / "computing" / etc. without explanation. **Fix:** "live preview" when stable, "recomputing…" when dirty. Phase F3.

### 9. DNA / Constitution hash have no tooltip explanation

The values `5937afd40a51` and `1cc073ea7f97e991a28612ea65b8f062ada8baf54c2eacb9f5a88b6428 3ed9f2` appear next to "DNA" and "CONSTITUTION HASH" labels. For a security-pro audience these are recognizable; for a non-technical evaluator they look like random noise. **Fix:** hover tooltip — *"Content-addressed identity. Same sliders always produce the same DNA."* Phase F3.

### 10. Constitution hash overflows column with mid-word line break

The 64-char hex string wraps mid-character at the column boundary, producing visually broken text. **Fix:** `word-break: break-all` plus a smaller font size or scrollable code-block style. Phase F3.

### 11. Audit tab rows have no payload drill-down

Each event renders as a single line: `#3  2026-04-25 18:22:51  agent_created  agent_name=VoiceTest role=network_watcher`. The full `event_data`, `entry_hash`, `prev_hash`, `agent_dna` aren't visible. For "trust the audit chain" to land, evaluators need to see the cryptographic linkage. **Fix:** click-to-expand each row to show full JSON + hash linkage. Phase F3 or F6.

### 12. Approvals tab title wraps awkwardly

"PENDING TOOL CALLS" wraps to two lines at the panel header — `PENDING / TOOL CALLS` looks like two separate labels. **Fix:** rename to "Approvals" or "Pending approvals" or `nowrap` style. Phase F3.

### 13. Tab labels are visually flat

No icons, no urgency cues except the (correctly-implemented) Approvals badge. For a non-technical audience the seven words don't telegraph what's behind each tab. **Fix:** add SVG icons (forge=hammer, agents=people, approvals=clipboard-check, skills=book, tools=wrench, memory=brain, audit=chain). Phase F3, ~2 hours of icon work.

### 14. No status bar — daemon health, agent count, recent activity all absent

The only health indicator is the provider dot in the top-right. Daemon health (`/healthz` startup_diagnostics) lives only as a JSON endpoint. For an evaluator wanting to know "is this thing working?" there's no surface. **Fix:** Phase F6 — bottom status bar showing daemon ✓, registry agent count, recent chain seq, last activity timestamp.

---

## P2 — nice-to-haves (only matter under sustained inspection)

### 15. Absolute dates instead of relative

Cards show `2026-04-25` rather than `3 days ago`. Mostly fine; relative would be friendlier for live demo.

### 16. Tier labels (PRIMARY / SECONDARY / TERTIARY) low contrast

The pill badges next to slider names are dim gray on dark gray. Readable but easy to miss the tier hierarchy.

### 17. No agent count summary in Agents tab

Should say "3 agents · 0 archived · 0 deferred" near the role/status filters.

### 18. No keyboard shortcuts

Tab navigation works with mouse only. No `1-7` for tab switching, no `cmd+k` for command palette.

### 19. Dark theme only

No light-mode toggle. Most security tools are dark-by-default; this matches convention but a toggle is a nice-to-have.

### 20. Empty-state CTAs are missing or weak

Empty Agents tab should say "No agents yet — start in the Forge tab." Empty Memory should say "No entries — agents will write here as they remember things." Empty Skills should say "Loading…" *and link to the install path* if the load fails.

---

## What's working well (don't break these)

- **The Forge tab itself** is genuinely well-laid-out. Sliders are clear, descriptions are concrete, the radar chart is striking, the live preview side panel is the right pattern.
- **Genre + role pickers** with filtering work crisply.
- **Audit tab event-type pills** are color-coded and readable (the `agent_created` green pill).
- **The accessibility tree** is clean — every tab and button has a proper `role` and label. ARIA is respected.
- **The provider switch button** in the top bar is a nice escape hatch even if the dot itself is alarming.
- **The badge counter** on the Approvals tab (the `—` that becomes a number when there are pending calls) is exactly the right pattern.
- **No console errors** during my walk-through (other than the 404 on agent detail). The error states that DO surface are caught and toast'd, not blown up.

---

## Triage summary by phase

The 6 P0s split between F3 (UI polish) and F4/F7 (demo data + scenarios):

| P0 | Lands in |
|---|---|
| 1. app.js missing skillsPanel + toolRegistryPanel start | **F3** (3-line code fix; immediate) |
| 2. Asset cache busting | **F3** (one-line per asset URL + optional nginx header) |
| 3. Agent detail 404 (instance_id mismatch) | **F3** (frontend + small daemon response shape change) |
| 4. Provider "unreachable" red dot by default | **F3** (rework health.js display logic) + **F8** (install script) |
| 5. Audit tab shows lifespan-only | **F4 + F7** (demo data dir, FSF_AUDIT_CHAIN_PATH env in start script) |
| 6. Swarm agents not in current registry | **F4 + F7** (pre-seeded demo registry) |

All P1s land in **F3** (UI polish) and **F6** (debug surface for #14).

Most P2s defer to a future polish pass.

---

## Recommended F3 starting order

1. **app.js boot fix** (3 P0 lines) — ship in 5 minutes, restores Skills + Tools tabs
2. **Asset cache busting** (P0 #2) — single deploy fixes returning-visitor experience
3. **Agent detail 404** (P0 #3) — paired list+detail change, ~30 LoC
4. **Provider status display** (P0 #4) — neutral "no provider configured" instead of red error
5. **Welcome banner** (P1 #7) — first-load context, dismissible
6. **Audit row drill-down** (P1 #11) — collapsible JSON, hash linkage visible
7. **Tab icons + status bar** (P1 #13, #14) — visual hierarchy + at-a-glance health
8. Remaining P1s in priority order

Total estimated F3 effort: 2-3 days for all P0s + half the P1s. The other P1s/P2s spread into F5 (tour) and F6 (debug surface).

---

## Sign-off

Demo-friction audit complete. The app's bones are good — clear architecture, accessibility-respecting markup, sensible empty states, proper error handling. The friction is concentrated in three places:

1. **A boot-time omission** that hides two whole tabs (P0 #1)
2. **Stale-cache behavior** that breaks returning visits (P0 #2)
3. **List/detail schema drift** that breaks the headline "click an agent" interaction (P0 #3)

Fix those three and the demo is *runnable*. The rest is polish that elevates "runnable" to "professional."
