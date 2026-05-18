# Forest Soul Forge — Demo Video Shooting Script v1

Five sections, 7-9 minutes total, designed to be shot as separate
takes and assembled in iMovie. Each section is self-contained so a
muffed take only requires re-recording that one section.

This script is written as if Claude were driving the Mac via
computer-control: every action labelled by app + visible element,
every narration line ready to read straight from the teleprompter.
Coordinates aren't pinned because window layouts shift; the
**bold UI labels** are the anchors.

## Pre-shoot checklist (do once before any recording)

- [ ] Mac mini at native resolution; if you scale, do it AFTER recording in iMovie's export step.
- [ ] **Focus** → Do Not Disturb ON. Suppresses notification popups.
- [ ] Menu bar cleaned via Control Center (hide Bluetooth, Wi-Fi if not central; keep the time visible).
- [ ] Daemon running (launchd should have it up 24/7 per B216). Verify via the green "local • ok" dot in the SoulUX header.
- [ ] SoulUX open at `http://127.0.0.1:5173` in its own browser window.
- [ ] Terminal window ready in a separate Space or minimized — bring forward only when shown.
- [ ] Registry has the demo state you want: at least one ready-to-talk agent + a swarm member. If cluttered with B210-era test agents, archive them or fork a clean demo data dir via `start-demo.command`.
- [ ] Recording: cmd+shift+5 → Options → **Show Mouse Clicks ON**, **Microphone = AirPods (or your best mic)**, **Save to ~/Movies/forge-demo/raw**.
- [ ] First-section timer-test: 30 second take, play back, confirm audio level + cursor highlight visible.

---

## Section 1 — "What this is" + first agent birth

**Target: ~90-100 seconds.** Opens cold, no setup explanation. The
visual carries the framing.

**Setup state:** SoulUX open on the **Forge** tab. Trait sliders
visible at neutral values. Browser is the frontmost window.

**Shot sequence:**

1. **Hold on Forge tab for ~3 seconds.** Cursor still.
   > "This is Forest Soul Forge — a local-first agent foundry. Every
   > agent born here is content-addressed: same trait profile, same
   > identity. Same identity, same audit trail. Watch."

2. **Drag the "Tactical aggression" slider** (or whichever has a clear visible label) **left to a low value, then right to a high value.**
   > "These sliders are 29 traits across six domains. They're not
   > prompt engineering — they shape an agent's constitution.
   > Constraints, allowed tools, posture, all derived from the
   > combination you set."

3. **Set role dropdown to `network_watcher`** (a swarm role with clear meaning).
   > "Role narrows what genre of agent this becomes. Each role
   > carries its own allowlist."

4. **Click "Birth".** Wait for completion toast.
   > "Click Birth. The forge produces four artifacts that all agree
   > on the same hash: soul, constitution, audit-chain entry, and
   > registry row. If any one drifts, the agent's identity is
   > broken — that's the integrity contract."

5. **Click "Agents" tab.** Newly-born agent appears in list.
   > "Newly-born agent shows up here. We never asked it to
   > register. Birth IS registration."

**Stop recording.**

---

## Section 2 — Forge a skill from natural language

**Target: ~100-120 seconds.** Shows the Skill Forge UI loop.

**Setup state:** SoulUX on the **Skills** tab.

**Shot sequence:**

1. **Hold on Skills tab.** Existing skill catalog visible.
   > "These are the skills agents can run — composable workflows
   > built from versioned tools. Today we'll add one without
   > writing any code."

2. **Click "New Skill" button.** Modal opens with description textarea.
   > "Operator-facing: type what you want, an LLM proposes a YAML
   > manifest grounded in the live tool catalog, you review, you
   > install."

3. **Click in the textarea and type slowly enough to be readable:**
   ```
   Read the last 50 lines of the audit chain and summarize the
   three most interesting events. Use llm_think for the summary.
   ```

4. **Click "Propose".** Wait for proposal to render.
   > "Behind the scenes — and this is the catalog-aware part — the
   > forge passes the live tool registry into the LLM's context.
   > The proposal can only reference tools that actually exist.
   > No hallucinations like 'fetch_audit_log' that don't ship."

5. **Hold on the proposed YAML for ~5 seconds** so viewers can read step IDs + references.
   > "Multi-step. Each step's output is wired into the next via
   > `${step_id.out.field}` references. That's the composition
   > primitive."

6. **Click "Install".** Confirmation lands.
   > "Installed. It's a normal skill now — versioned, dispatchable,
   > visible in the catalog."

7. **(Optional) Click "Run" with default inputs, show a 1-2 line result.** Skip if pacing is tight.

**Stop recording.**

---

## Section 3 — Per-tool allowances (the ADR-0053 feature, freshest)

**Target: ~90 seconds.** This is the differentiator — granular
trust, not all-or-nothing.

**Setup state:** SoulUX on the **Chat** tab. An assistant agent's
session open. The "Computer-control allowances" card visible
(scroll down inside the assistant settings if needed).

**Shot sequence:**

1. **Hold on the allowances card.**
   > "Most agent frameworks ask: do you trust this agent or not?
   > Forest asks a different question — which specific tools do
   > you trust this agent to run, and under which posture?"

2. **Click "Restricted" preset button.** Status line updates to "not granted."
   > "Restricted: assistant has zero computer-control tools. Can't
   > see your screen, can't click anything, can't open apps."

3. **Click "Specific" preset.** Status updates to show 2 per-tool grants.
   > "Specific: seeds two tools — read the screen, read the
   > clipboard. The assistant can OBSERVE but not act."

4. **Click "Advanced — per-tool toggles" disclosure to expand.** The 6-row checkbox grid is now visible.
   > "And this is the granularity. Each tool has its own toggle.
   > Coverage column shows where the grant comes from — per-tool
   > override or via plugin-level."

5. **Click the checkbox for `computer_click.v1`.** Toast confirms.
   > "Toggle that on — now the assistant can click, in addition
   > to the read tools. Granular. Specific. Recorded."

6. **Click "Audit" tab quickly.** Show the latest two events at the top: `agent_plugin_granted` with `tool_name` populated.
   > "Every grant lands in the audit chain. The audit lens shows
   > exactly which tool the operator granted, which tier, at what
   > seq."

**Stop recording.**

---

## Section 4 — Audit chain integrity

**Target: ~90 seconds.** Shows the spine of the whole system.

**Setup state:** SoulUX on the **Audit** tab. Audit chain visible
with recent events.

**Shot sequence:**

1. **Hold on Audit tab.** Recent events scroll into view.
   > "Everything an agent does — and everything an operator does
   > on an agent's behalf — lands here. JSONL, hash-linked, append-
   > only. Same posture as a blockchain except local-only and not
   > pretending to be money."

2. **Click the most recent event to expand it.** Hash + prev_hash visible.
   > "Each entry includes the prior entry's hash. Tamper with any
   > line in the middle, every subsequent entry's hash check fails.
   > That's how we know it wasn't edited after the fact."

3. **Switch to Terminal (cmd+tab or Mission Control).** Run:
   ```
   curl -s -H "X-FSF-Token: $(grep FSF_API_TOKEN .env | cut -d= -f2)" \
        http://127.0.0.1:7423/agents/<some_id>/tools/call \
        -d '{"tool_name":"audit_chain_verify","tool_version":"1","tool_args":{},"tool_version_for_audit":"1","session_id":"demo-verify"}' \
        -H "Content-Type: application/json"
   ```
   (Or use a pre-staged `verify-chain.command` script to keep the take clean.)

4. **Show the JSON response with `ok: true` and entries verified.**
   > "The verifier walks the whole chain, recomputes every hash,
   > and confirms the linkage. Forest verifies its own integrity
   > with its own tool. The substrate IS the verification surface."

5. **Switch back to SoulUX Audit tab.**
   > "This isn't a feature we marketed at — it's the contract
   > everything else depends on. Without this, every other claim
   > the foundry makes is unverifiable."

**Stop recording.**

---

## Section 5 — Closing: marketplace + what's next

**Target: ~60-75 seconds.** Soft close, points to the road ahead.

**Setup state:** SoulUX on the **Marketplace** tab.

**Shot sequence:**

1. **Hold on Marketplace tab.** Browse list visible.
   > "We just shipped the marketplace seed catalog: ten composable
   > tools, ten skills. Operators index plug-and-play content from
   > here — install with one click."

2. **Search for "summarize" or similar** to surface a relevant seed item.
   > "Search filters by name, description, capability tag, or
   > side-effects ceiling. Useful when you've grown past 50
   > entries and need to find the one you remember."

3. **Click an entry → "Install".** Show the post-install grant picker (B229).
   > "Install is one action. The next prompt asks which agent should
   > be granted access. Marketplace + grants are wired together —
   > installing without granting just adds dead code to your kernel."

4. **Click "Grant" against one of the agents in the list.**
   > "Granted. The tool is live for that agent. The audit chain
   > shows the install AND the grant as two distinct events."

5. **Pull camera (just pan-zoom in iMovie) to show the green local-ok dot in the SoulUX header.**
   > "All of this ran on the Mac mini sitting next to me. Zero
   > cloud, zero telemetry. The kernel ships open source, the
   > marketplace ships open content. Forest is the audit-grade
   > local-first agent substrate. Thanks for watching."

**Stop recording.**

---

## Post-production in iMovie

1. Open iMovie → File → New Movie → import the 5 .mov files from `~/Movies/forge-demo/raw`.
2. Drag onto timeline in order 1 → 5.
3. Trim each clip's head/tail (drag clip edges inward) to remove the "I just pressed record" and "let me stop recording" moments.
4. Drop a title card between sections: Titles tab → "Standard Lower Third" → set text to "Section N — <title>".
5. Music (optional): Audio tab → built-in iMovie loops have a "Modern → Tech" set that fits. Volume to ~15% so narration carries.
6. Audio normalize: Modify menu → Auto-Enhance audio. Or set each clip's level to about -6dB peak by ear.
7. Export: File → Share → File → 1080p, High quality. Lands as a single .mp4.

If you want individual section files instead of the assembled video:
- After step 4 above, select each clip + its title card.
- File → Share → File → Selection. Export per-section.
- You get five 1-2 minute .mp4 files, useful for social cuts or doc pages.

---

## Notes on what to skip and why

- **No deep dive on the Security Swarm chain.** It's the most technically impressive feature but takes 3-4 minutes to demo properly and would unbalance a 7-minute video. Save for a follow-up "Forest Security Swarm in 90 seconds" cut.
- **No live forging of a tool.** Tool Forge UI is a sibling of Skill Forge — showing both is repetitive for a first-time viewer.
- **No drift sentinel / conformance suite.** These are operator-credibility features, not first-impression features.
- **No comparison to LangChain/AutoGPT.** Resist the urge. Show what Forest does well; let the audience draw the contrast.

## If a section runs long

Drop in order of priority: Section 5 (marketplace) → Section 4 (audit) → Section 2 (skill forge). Section 1 (birth) and Section 3 (per-tool toggles) are the load-bearing demos that distinguish the foundry — keep those even if total runtime stretches to 10 minutes.

## Sources

- ADR-0001 D2 — identity invariance (used in Section 1 narration)
- ADR-0033 — Security Swarm (referenced in skip notes)
- ADR-0053 D3 + D5 — per-tool granularity + preset semantics (Section 3)
- ADR-0044 — kernel positioning + SoulUX (closing line)
- STATE.md current numbers — 2,800 unit tests, 76 audit event types, etc. (reference if you want to throw stats into the intro)
