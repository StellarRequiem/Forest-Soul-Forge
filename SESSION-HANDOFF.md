# Session handoff — 2026-05-05 → next session

**Use this document as the entry point for the next Cowork session.** Open
a new chat with Claude and paste:

> Read `/Users/llm01/Forest-Soul-Forge/SESSION-HANDOFF.md` and
> `/Users/llm01/Library/Application Support/Claude/local-agent-mode-sessions/076ee539-f869-4b56-9096-fa3777a45681/87fd4f13-863d-41b1-ada3-c935492c499c/spaces/6bd3ae6b-ecf0-4a98-a6df-c4c409bfa690/memory/MEMORY.md`,
> then continue from where the previous session left off.

The auto-memory persists across sessions automatically; this document is
the conversation-state bridge.

---

## 1. Repository state at handoff

- **HEAD:** `97510c2` on `main`
  *test+spec: 7 integration tests + §2.2 audit chain canonical-form drift fix (B134)*
- **Remote:** `origin` at `https://github.com/StellarRequiem/Forest-Soul-Forge`, fully pushed
- **Working tree:** clean except for this handoff file
- **Test suite:** 2,409 passing, 3 skipped (sandbox-only), 1 xfail (v6→v7 SQLite migration, pre-existing per Phase A audit F-7)
- **Conformance suite:** at `tests/conformance/` — runnable via `pip install "forest-soul-forge[conformance]" && pytest tests/conformance/`
- **Hardware:** Mac mini 2024, Apple M4, 16 GB unified memory, 250 GB SSD, macOS Tahoe 26.3 (`Alexander price` Apple Account, `LLM's Mac mini` hostname)

## 2. What this session shipped (Bursts 124-134, 11 commits)

| Burst | SHA | Subject |
|---|---|---|
| 124 | `5b1eaaf` | feat(roles): role inventory 18 → 42 (24 new + 4 renames) |
| 125 | `41cfb53` | docs: STATE.md refresh, Bursts 116-124 |
| 126 | `9d17bb2` | chore: housekeeping (verifier_loop archetype + Phase G ownership + audit chain sync) |
| 127 | `564ed52` | docs(spec): formal kernel API spec v0.6 (ADR-0044 P2) — 1,042 lines |
| 128 | `a45d117` | chore: archive 100 commit-* + tag-* scripts to dev-tools/commit-bursts/ |
| 129 | `5370517` | feat(kernel): true headless mode + SoulUX clarification (ADR-0044 P3) |
| 130 | `2c4b488` | feat(conformance): kernel API conformance test suite scaffold (ADR-0044 P4) |
| 131 | `668224d` | docs: integrator pitch + quickstart (ADR-0044 P6 outreach materials) |
| 132 | `6dc7729` | feat(conformance): manifest validation + idempotency probe + markdown report |
| 133 | `27cfa80` | feat: JSONSchema input defaults at runtime + frontend test scaffold |
| 134 | `97510c2` | test+spec: 7 integration tests + §2.2 audit chain canonical-form drift fix |

**Notable B134 finding:** The kernel API spec §2.2 originally documented the wrong canonical-JSON form (claimed timestamp was hashed; claimed seq=1 prev_hash was all-zeros). Reality: timestamp is excluded for clock-skew protection, and genesis prev_hash is literal `"GENESIS"`. Conformance test §2.2 implemented the buggy spec — would have failed against any real Forest-kernel build. Both spec and test fixed in B134 before any external integrator outreach started.

## 3. v0.6 kernel arc (ADR-0044) — phase status

| Phase | Status | Where |
|---|---|---|
| P1 boundary doc + KERNEL.md + sentinel | ✅ Bursts 118-120 | `docs/architecture/kernel-userspace-boundary.md`, `KERNEL.md`, `dev-tools/check-kernel-imports.sh` |
| P2 formal kernel API spec | ✅ B127 | `docs/spec/kernel-api-v0.6.md` |
| P3 headless mode + SoulUX split | ✅ B129 | `docs/runbooks/headless-install.md`, `scripts/headless-smoke.sh` |
| P4 conformance test suite | ✅ B130 + B132 | `tests/conformance/` |
| P5 license + governance ADR-0046 | ✅ B121 | `docs/decisions/ADR-0046-license-and-governance.md` |
| P5.1 CONTRIBUTING + CoC | ✅ B122 | `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` |
| P6 outreach materials | ✅ B131 | `docs/integrator-pitch.md`, `docs/integrator-quickstart.md` |
| P6 actual integrator validation | ⏳ months not bursts | Recruiting effort — load-bearing for v1.0 freeze |
| P7 v1.0 stability commitment | ⏳ gated on P6 | When external integrator reports back PASS on conformance |

The arc has reached its **natural pause point** for burst-deliverable work. Everything that can ship without external feedback has shipped.

## 4. What we're MID-FLIGHT on (resume here)

### 4a. LocalLLaMA Discord outreach (P6 execution)

**Status:** 3 message variants drafted; Alex hasn't picked one yet, hasn't named the specific Discord server/channel, hasn't approved sending.

**Drafts** (to repaste / refine):

- **Variant A** (~150 words, technical + concrete) — leads with primitives bullet list; best for `#projects-and-showcase`-style channels
- **Variant B** (~80 words, conversational) — short ask; best for high-volume general chat
- **Variant C** (~100 words, leading question) — leads with "anyone else doing hash-chained audit for local agents"; best for engagement

**What's needed from Alex:**
1. Which variant (A / B / C / hybrid)
2. Which Discord server + channel name
3. Permission to drive the UI (paste-only, leave Send for him to hit per explicit-permission rules)

**Constraint:** Discord is third-party native app, full tier when granted. I can paste but per `action_types` rules, **never hit Send for the user**.

### 4b. 24/7 ops setup — Layers 1/2/3 (NOT YET EXECUTED)

Alex asked how to free overhead and run specialist agents 24/7 on his 16 GB M4 Mac mini. I outlined a 3-layer recipe; nothing has been touched on his system yet.

**Layer 1 — macOS overhead reduction (no deletes, ~2 GB freed):**
- System Settings → General → Login Items: disable Spotify Helper / Microsoft AutoUpdate / Adobe / Dropbox / vendor updaters
- System Settings → Apple Intelligence & Siri: disable Apple Intelligence (preloads ~2 GB of models), disable "Listen for Hey Siri"
- System Settings → Spotlight → Search Privacy: add Forest-Soul-Forge folder + Downloads + repos to exclusion list
- System Settings → AirDrop & Handoff: disable Handoff, Continuity Camera, AirPlay Receiver, AirDrop
- System Settings → Accessibility → Display: enable Reduce Motion + Reduce Transparency
- System Settings → Notifications: disable for non-essential apps
- System Settings → Apple Account → iCloud: disable Photos sync, iCloud Drive auto-download for non-essentials
- Quit (don't delete) Mail, Messages, FaceTime, Photos, Music, TV when not in use

**Layer 2 — Process priority + Ollama tuning:**
```bash
# After Ollama starts:
sudo taskpolicy -c user-interactive -p $(pgrep -f "ollama serve")
# After Forest daemon starts:
sudo taskpolicy -c user-interactive -p $(pgrep -f "forest_soul_forge.daemon")

# In .env at repo root — pin model in memory:
OLLAMA_KEEP_ALIVE=-1
OLLAMA_NUM_PARALLEL=1
```

**Layer 3 — launchd auto-start + KeepAlive:**
- Create `~/Library/LaunchAgents/dev.forest.ollama.plist` (template provided in conversation)
- Create `~/Library/LaunchAgents/dev.forest.daemon.plist` (template provided)
- `launchctl load ...` both
- System Settings → Battery (called Energy on Mac mini): "Prevent automatic sleeping when display is off" + "Start up automatically after a power failure" + "Wake for network access"

**Memory budget after L1+L2+L3:**
- Lean macOS: ~3 GB
- Ollama (idle): ~150 MB
- One 7B model loaded with KEEP_ALIVE=-1: ~5 GB
- Forest daemon: ~500 MB - 1 GB
- **Total used: ~8.5-9 GB. Free: ~7 GB.**

**Open question for Alex:** does he want Claude to drive computer-use through L1+L2 visually, or just hand him the launchd plists with paths filled in?

### 4c. Model installation (NOT YET DONE)

**Recommended stable for 16 GB M4 (Forest-tuned, May 2025 cutoff):**
- **Qwen 2.5 7B Instruct** at Q4_K_M (~4.7 GB) — daily driver; strong JSON for tool use
- Optional: **Llama 3.1 8B Instruct** at Q4_K_M (~4.9 GB) — alternative; widely tested

**Not yet checked:** what's actually installed via `ollama list`. Sandbox can't reach Ollama (different loopback); user needs to run the command. Terminal is click-tier so Claude can't type — Alex runs, Claude screenshots the output.

**Forest config to set after install** (in `.env` at repo root):
```bash
FSF_LOCAL_MODEL=qwen2.5:7b
FSF_LOCAL_MODEL_TOOL_USE=qwen2.5:7b
FSF_LOCAL_MODEL_CLASSIFY=qwen2.5:7b
FSF_LOCAL_MODEL_GENERATE=qwen2.5:7b
FSF_LOCAL_MODEL_CONVERSATION=qwen2.5:7b
OLLAMA_KEEP_ALIVE=-1
```

**Deferred forward-looking item:** LocalLLaMA Discord likely has 2025-2026 model recommendations beating my May 2025 cutoff (Qwen 3, Llama 4, etc.). Alex was planning to ask the channel after the outreach post lands.

### 4d. Specialist agent stable for 24/7 work (DESIGN ONLY, not births)

Forest's role inventory (post-B124) supports 24/7 operations natively. Recommended stable to birth once Layer 1/2/3 is set up:

- **`dashboard_watcher`** (observer) — polls health endpoints / GitHub status / system metrics. Read-only, cheap, scheduled cron.
- **`signal_listener`** (observer) — tails log streams for anomalies; passes events through `llm_think` for classification.
- **`incident_correlator`** (investigator) — runs every N minutes, cross-references log windows for patterns.
- **`paper_summarizer`** + **`vendor_research`** (researcher) — sleep-time fetch+summarize feeds; memory-write to lineage scope so Alex wakes to a digest.
- **`status_reporter`** (communicator) — daily 7am dispatch of "what happened in the audit chain since yesterday."

ADR-0041 (Set-and-Forget Orchestrator) is the substrate for the cron-like cadence. Agents themselves cost nothing when idle (rows + soul artifacts on disk); the RAM cost is the one model loaded by Ollama plus Forest's ~500 MB Python footprint.

## 5. STATE.md backlog status

Most v0.6 backlog items are now ✅. What remains:

- ⏳ **ADR-0042 T5** — Tauri code-signing + auto-updater. **Gated on Apple Developer account decision** (Alex's call).
- ⏳ **ADR-0043 follow-up #4** — `plugin_secret_set` audit event. **Gated on secrets-storage decision**.
- ⏳ **ADR-0036** cross-agent contradiction scan. Deferred to v0.4 per ADR trade-offs.
- ⏳ **ADR-0038 T4-T6** — telemetry/disclosure_intent_check/external_support_redirect. Deferred to v0.3 per ADR status.
- ⏳ **`mfa_check.v1`** — operator hasn't scoped "MFA posture" target.

## 6. Operating conventions discovered this session

These belong in the next session's working memory:

- **Terminal is `click` tier** (can click but not type). Run shell commands via Bash tool, not via Terminal UI.
- **Finder is `full` tier**. Type-jump + cmd+O is the fast way to launch `.command` scripts: click in Finder file list → type partial filename → press cmd+O. Much faster than scrolling.
- **`cmd+shift+G`** opens "Go to Folder" in Finder. Type any absolute path to navigate. Useful for jumping into `dev-tools/commit-bursts/` directly.
- **`.git/index.lock` recreates after every commit**. Sandbox can't `rm` it (Operation not permitted). User must run `./clean-git-locks.command` on host before each commit cycle. The script uses AppleScript to remove the lock file at host privilege.
- **Commit scripts now live in `dev-tools/commit-bursts/`** post-B128. Each script does `cd "$(dirname "$0")/../.."` to navigate from archive back to repo root before running git. The convention: future commit-burst* scripts land directly in the archive folder.
- **Audit chain canonical form** (per B134 fix): `entry_hash = sha256(canonical_json({seq, agent_dna, event_type, event_data, prev_hash}))` — timestamp is NOT hashed, genesis prev_hash is literal `"GENESIS"`. Don't repeat the spec drift.
- **Forest defaults `audit_chain_path` to `examples/audit_chain.jsonl`** (kernel-adjacent seed state per the boundary doc). `data/audit_chain.jsonl` is the dev fixture. CLAUDE.md documents this.
- **Test suite is 2,409 unit + integration tests** (was 2,386 pre-session). Conformance suite runs separately against a live daemon via `pytest tests/conformance/`.
- **Bash is the right tool for git operations from sandbox**. Computer-use Terminal is for visual confirmation, not command execution.
- **Drift sentinel** at `dev-tools/check-drift.sh` runs every numeric claim against disk reality. Run before any release tag.

## 7. Pending operator decisions awaiting Alex

1. **LocalLLaMA Discord channel + post variant** (4a above)
2. **Layer 1/2/3 execution** — drive together via computer-use, or hand Alex the recipe text? (4b)
3. **Model install** — what does `ollama list` currently show? Run + screenshot. (4c)
4. **Apple Developer account** — gates ADR-0042 T5 (Tauri code-signing + auto-updater)
5. **Plugin secrets storage** — gates ADR-0043 follow-up #4

## 8. Where to look first (next session)

| If user asks about... | Read |
|---|---|
| Project state / numbers | `STATE.md` |
| Kernel API contract | `docs/spec/kernel-api-v0.6.md` |
| Architectural boundary | `docs/architecture/kernel-userspace-boundary.md` |
| Strategic posture | `docs/decisions/ADR-0044-kernel-positioning-soulux.md` |
| What changed recently | `git log --oneline -20` |
| Operating conventions | `CLAUDE.md` |
| Live task list | This file's §4 + auto-memory |
| Conformance behavior | `tests/conformance/README.md` |
| Headless install | `docs/runbooks/headless-install.md` |
| Outreach materials | `docs/integrator-pitch.md`, `docs/integrator-quickstart.md` |

## 9. Verification commands the next session can run

```bash
# Where are we?
cd /Users/llm01/Forest-Soul-Forge
git log --oneline -5

# Test suite green?
PYTHONPATH=src python3 -m pytest tests/unit/ tests/integration/ -q -n 4 | tail -3

# Working tree clean?
git status --short

# What needs the user's running daemon to verify?
./scripts/headless-smoke.sh
```

If all four come back clean, the project is exactly where this session left it.

## 10. Last conversational state

The last thing the previous session said to Alex (verbatim, condensed):

> Want me to drive computer-use through Layers 1+2 with you so you can
> see what each toggle does on screen, or to draft the actual launchd
> plists with your specific paths filled in?

Alex responded by asking for this handoff doc instead. The 24/7 ops setup is the natural resume point: ask whether to drive L1+L2 visually or hand him the plists.
