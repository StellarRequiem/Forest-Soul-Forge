# External review — read this first

This doc is for someone reviewing Forest Soul Forge from outside.
Read it before opening other files. It captures what's load-bearing,
what's open, what's deliberately deferred, and where the prior
review's artifacts live so you can see what's already been reasoned
about.

**Last updated:** 2026-05-01, post-v0.1.2 release + Bursts 46–49 + ADR drafts 35/36/37 + this doc (Burst 50).

---

## Snapshot in 60 seconds

| | |
|---:|:---|
| **Latest tag** | `v0.1.2` (commit `5deb3ca`, 2026-05-01) — SarahR1 absorption release |
| **HEAD** | varies; see `git log -1 --oneline` for current |
| **Test suite** | 1589 unit passing + 5 integration; 0 regressions through the absorption arc |
| **Test growth** | 992 (v0.1.0) → 1439 (v0.1.1) → 1567 (v0.1.2) → 1589 (Bursts 46/49) |
| **Schema** | v11 |
| **ADRs filed** | 35 (ADR-0001 → ADR-0038, with gaps + amendments + drafts) |
| **Tools registered** | 41 (12 carry explicit initiative annotations as of Burst 49) |
| **Genres** | 13 (7 original + 3 security tiers + 3 web tiers) |
| **Read this fastest** | `STATE.md` for the developer's view, `README.md` for the product view |

## What changed since the last external review

The 2026-04-30 review by [SarahR1 (Irisviel)](https://github.com/SarahR1) was absorbed across the v0.1.2 release arc + Bursts 46–49. Three Proposed ADRs were promoted to Accepted; three more were drafted as Proposed for v0.3 to capture pre-decided design choices.

| Surface | Before review | After v0.1.2 + post-release Bursts |
|---|---|---|
| **Companion safety** | trait emphasis + privacy floor | + harm taxonomy (8 harms), `min_trait_floors` mechanic, voice safety filter, §honesty constitutional template |
| **Memory epistemics** | 4 privacy scopes + K1 verified bit | + `claim_type` (6-class enum), 3-state `confidence`, `memory_contradictions` table, `memory_challenge.v1` tool |
| **Initiative ladder** | side-effects ceiling only | + L0–L5 ladder per genre, dispatcher floor step, 12 tools annotated |
| **External attribution** | no formal pattern | `CREDITS.md` discipline; per-ADR cited catalysts; saved response of record |
| **v0.3 queue** | implicit roadmap items | ADR-0035 Persona Forge + ADR-0036 Verifier Loop + ADR-0037 Observability filed as Proposed |

If you want the per-ADR delta, read:
- [`CHANGELOG.md`](../CHANGELOG.md) [0.1.2] entry — full ledger
- [`CREDITS.md`](../CREDITS.md) — attribution + adopted/declined ledger
- [`docs/audits/2026-05-01-sarahr1-review-response.md`](audits/2026-05-01-sarahr1-review-response.md) — the response of record (covers stale-claim corrections, adoptions, push-backs, questions)

---

## Load-bearing invariants — please don't propose breaking these

These are documented in `CLAUDE.md` and enforced via tests. A review that proposes changing any of them needs concrete evidence that it makes the Forge strictly better — see the §0 Hippocratic gate (also in `CLAUDE.md`).

1. **Audit chain is append-only and hash-linked.** Every state change → one chain entry. The chain is the source of truth; the registry is rebuildable from it. (ADR-0009)
2. **DNA is content-addressed.** Same trait profile → same DNA. Don't propose changing the DNA derivation without a major version bump and migration plan. (ADR-0001 / ADR-0002)
3. **Constitution hash is immutable per agent.** Adding fields to canonical_body is acceptable (the v1 `genre` field landing in v0.0 → v0.1 set the precedent; ADR-0021-am added `initiative_level` + `initiative_ceiling` similarly). Mutating an existing agent's hash is not. (ADR-0001 + ADR-0004)
4. **`body_hash` survives Y7 purge.** After lazy summarization removes a turn body, `body_hash` (SHA-256 of the original) stays for tamper-evidence. (ADR-003Y Y7)
5. **Single-writer SQLite discipline.** All write paths go through `app.state.write_lock` (a `threading.RLock`). Don't add new writers that bypass this.
6. **Genre kit-tier ceiling.** A role's resolved tools must not exceed `genre.max_side_effects`. Birth + dispatch both check.
7. **Genre privacy floor.** Companion-genre memory writes default to `private` and cannot be widened. Operator override via `memory_scope_override` event. (ADR-0027 §5)
8. **Local-first.** No telemetry, no phone-home. Daemon binds to 127.0.0.1 only by default.

---

## Where to look for "this was already discussed"

If you have an opinion about a thing, please check whether the project already has an opinion + reasoning before pushing back. Common surfaces:

| Topic | Where to look |
|---|---|
| Why Forest Soul Forge exists | `README.md` mission section + ADR-0024 (project horizons) |
| Soul vs constitution | ADR-0001 + ADR-0004 + STATE.md "Soul vs constitution" section |
| Audit chain semantics | ADR-0009 + `core/audit_chain.py` docstring |
| Memory privacy | ADR-0027 + ADR-0027 amendment |
| Tool dispatch flow | ADR-0019 + R3 `governance_pipeline.py` |
| Genre taxonomy | ADR-0021 + ADR-0033 + `config/genres.yaml` |
| Why no XYZ feature | The roadmap → the v0.2 close plan → the explicit "deferred" sections in each ADR |
| Why this trait/genre decision | ADR-003Y open questions + ADR-0021 open questions |
| Why we declined Sarah's specific suggestion X | `CREDITS.md` "Declined from her review (with reasoning)" |
| External attribution discipline | `CREDITS.md` house rules |

If you find something that you'd expect to be discussed but isn't, that's a useful finding — file it as a question.

---

## Active gaps + things that are deliberately incomplete

To save you the cost SarahR1 paid (a 2-3 day stale snapshot in places), here are the things that are **known incomplete but on purpose** at v0.1.2.

### Implementation gaps (queued for v0.3 per ADR-0035 / ADR-0036 / ADR-0037)

- **Persona Forge layer doesn't exist yet.** ADR-0035 specifies the design; no implementation in v0.1.2. Mutable per-agent runtime state lives in memory entries; ratifiable persona proposals are v0.3.
- **No automated memory contradiction detection.** ADR-0036 specifies the Verifier Loop; no Verifier agent role exists. Contradictions are operator/agent-supplied via `memory_challenge.v1` only.
- **No operator dashboard.** ADR-0037 specifies the Observability tab. Today's frontend has Birth / Agent / Audit / Chat tabs; no `/observability` tab.

### Implementation gaps that are NOT necessarily v0.3

- **Companion-tier real-time A/V** — Mission Pillar 2 from STATE.md. Genre exists structurally; voice / vision / WebRTC bits don't.
- **Federation / Horizon 3** — `realm` memory scope is reserved but unreachable. Cross-machine agent communication is post-v1.0.
- **Frontend tests** — Vitest scaffold proposed in roadmap T-11 but not landed.

### Deliberate decisions that may look like gaps

- **Initiative annotation only on 12 of 41 tools.** Read-only memory tools (memory_recall) have no initiative requirement because the genre's max_side_effects ceiling already gates them. Adding annotations to every tool was rejected as opt-in-still-meaningful (Burst 46 + 49 covered the heaviest 12).
- **Embodied / interoceptive state (energy_budget, attention_load, etc.)** — declined per ADR-0038 §4. Adds attack surface and risks H-2 (false sentience claims). Document in CREDITS.md "Declined from her review."
- **Skill engine compile_arg dict/list — fixed pre-v0.1.0.** A snapshot from before 2026-04-28 commit `04c0d27` would say this is a blocker. It isn't.
- **Soul.md as content-addressed artifact.** Sarah's review framed this as a misread (response §3); the architecture is correct, what's missing is the layered identity layer (ADR-0035).

---

## Ground rules for proposing changes

1. **Verify before proposing.** Ground claims in actual repo state, not a stale snapshot. The `git log` and `STATE.md` are the source of truth for what's currently shipped.
2. **§0 Hippocratic gate for removals.** Every "rip out X" needs (a) prove harm; (b) prove non-load-bearing; (c) prove the alternative is strictly better. If any fails, leave with a comment.
3. **External attribution discipline (CREDITS.md).** If your review lands in the codebase, you'll get credit + the adopted/declined ledger pattern applies. If you have prior art, cite it.
4. **One bite at a time.** Big-bang reviews that propose 30 changes simultaneously are hard to absorb cleanly. SarahR1's review was sized at ~10 substantive items + ~5 push-backs; that was a workable absorption arc.
5. **Disagreement is fine.** The SarahR1 absorption included three explicit push-backs — declined items with reasoning. The point is to get the architecture right, not to agree on every framing.

---

## Quick orientation: directory map

```
Forest-Soul-Forge/
├── src/forest_soul_forge/      # ~36k LoC Python; the daemon + tools + core
│   ├── core/                   # trait engine, constitution, dna, audit_chain,
│   │                           # genre_engine, memory, tool_catalog, tool_policy
│   ├── daemon/                 # FastAPI app, routers, schemas, providers, deps
│   ├── tools/                  # dispatcher, governance_pipeline, builtin/
│   ├── soul/                   # voice_renderer, voice_safety_filter
│   ├── chronicle/              # rendering soul.md narrative
│   ├── forge/                  # tool_forge + skill_forge agent-driven creators
│   ├── registry/               # SQLite schema + tables
│   ├── security/               # ADR-0033 Security Swarm primitives
│   ├── agents/                 # placeholder for v0.3 agent factory work
│   └── ui/                     # placeholder for future browser-based UI
├── tests/                      # 1589 unit + 5 integration cases
├── config/                     # genres.yaml, tool_catalog.yaml, trait_tree.yaml,
│                               # constitution_templates.yaml, skills/, scenarios/
├── docs/
│   ├── decisions/              # 35 ADRs (Proposed + Accepted + Deferred)
│   ├── audits/                 # 2026-04-30 comprehensive audit + 2026-05-01 SarahR1 response
│   ├── architecture/           # layout.md + cross-references
│   ├── runbooks/               # operator-facing per-track runbooks
│   ├── roadmap/                # v0.2-to-v1.0 + v0.2 close plan
│   └── external-review-readiness.md   ← you are here
├── frontend/                   # ~22 vanilla JS modules; FastAPI serves it on :5173
├── examples/                   # reference births + scenarios
├── scenarios/                  # demo data sets for `load-scenario.command`
├── CLAUDE.md                   # harness conventions (the development arm reads this)
├── STATE.md                    # developer-facing snapshot
├── README.md                   # product-and-mission view
├── CHANGELOG.md                # release-by-release ledger
├── CREDITS.md                  # external contributor attribution + ledger
└── *.command                   # macOS Finder-launchable operator scripts
```

If you want to start with the runtime: `start.command`. If you want to start with the architecture: ADR-0001 → ADR-0033 in numerical order.

---

## Specific things that benefit from external eyes

These are the surfaces where a review's signal-to-noise is highest. The list is intentional and reflects where the project is genuinely uncertain.

1. **ADR-0035 / 0036 / 0037 designs.** All three are Proposed, not yet implemented. A reviewer reading them might catch a flaw before we build on the wrong shape.
2. **Companion-tier harm taxonomy completeness.** ADR-0038 names eight harms. Are there others? H-9 / H-10 candidates worth considering for ADR-0038's open questions section.
3. **Per-tool initiative annotation policy.** 12 of 41 tools have explicit annotations. Should more? Should some current annotations move (e.g. `code_edit` is L4; some operators may want L3 with policy)?
4. **The v0.3 ADR queue itself.** ADR-0035 / 0036 / 0037 are the three filed; what else is missing from the v0.3 queue?
5. **External federation (Horizon 3) when it eventually lands.** The threat model (ADR-0025) is sketched; the protocol (ADR-0028 data portability + ADR-0029 regulatory) is sketched; nothing is implemented. v1.0+ work, but design feedback now is cheap.

## Where the project is reasonably stable + needs less review

1. **Audit chain semantics + verification.** ADR-0009 + tests; well-trodden ground.
2. **Tool dispatcher governance pipeline.** ADR-0019 + R3 refactor; recently refactored, well-tested.
3. **Memory privacy contract.** ADR-0027 + amendment; the data layer is solid.
4. **Birth-time identity (DNA + soul + constitution).** ADR-0001 + ADR-0004; load-bearing and immutable by design.

A review that focuses time on these surfaces is welcome but lower-yield than time spent on the gap-list above.

---

## Closing note

This codebase is open to genuine peer review. The SarahR1 absorption arc demonstrated the pattern: review → ADRs as Proposed → implementation tranches → promotion to Accepted → release tag → response of record. CREDITS.md captures who shaped what.

If you're considering reviewing FSF: thank you in advance. The format we found useful is:
- 2-3 days of reading time minimum
- A written analysis (not real-time)
- ~10 substantive observations + ~5 push-backs on existing decisions
- Tagged confidence per claim ("I'm sure," "I think," "I'd want to verify")

The goal is to make the Forge strictly better. Push-back, disagreement, and challenge are welcomed when grounded.
