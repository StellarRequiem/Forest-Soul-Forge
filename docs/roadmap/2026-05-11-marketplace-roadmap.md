# Forest Marketplace — phased roadmap

**Date:** 2026-05-11
**Reference ADR:** [ADR-0055](../decisions/ADR-0055-agentic-marketplace.md) (Proposed, expanded 2026-05-11)
**Status:** plan — no implementation has begun. Kernel-side foundations (ADR-0043 plugin protocol, ADR-0060 runtime tool grants, B225 HTTP transport) are landed; marketplace integration tranches M1–M10 are queued.

## What this roadmap is

ADR-0055 defines what the marketplace IS. This document sequences M1–M10 into delivery phases with explicit dependency edges, splits work between the kernel and the (not-yet-existing) `forest-marketplace` sibling repo, and surfaces decisions that must land before specific tranches can ship.

## Pre-conditions that ARE met

- **ADR-0043 plugin protocol** — `.plugin` manifest format, `~/.forest/plugins/installed/` filesystem layout, hot-reload via `POST /plugins/reload`. Shipped Bursts 104-107.
- **ADR-0060 runtime tool grants** — `POST /agents/{id}/tools/grant` + posture × trust_tier matrix. Shipped Bursts 219-223.
- **B225 HTTP transport** — `mcp_call.v1` handles `http://` and `https://` URLs in addition to `stdio:`. Any spec-compliant MCP is operable.
- **Plugin grants table** (`agent_plugin_grants`) and per-tool grant table (`agent_catalog_grants`). Both audited.
- **Audit chain** with 75 event types and tamper-evident hashing.

## Pre-conditions NOT yet met (open decisions)

These have to resolve before specific tranches; called out next to the tranche that needs each.

1. **`forest-marketplace` sibling repo doesn't exist.** Needs `git init` somewhere, with `README` + `CONTRIBUTING` + initial schema files. Owner identity decision: is it `StellarRequiem/forest-marketplace` (Alex personal account) or a forest-team org? **Blocks M2.**

2. **Maintainer ed25519 keypair.** Used to sign marketplace entries. Generation + secure storage procedure + publication-of-public-key path. **Blocks M6.**

3. **Telemetry submission endpoint.** GitHub Pages can serve `marketplace.yaml` (static) but cannot accept POSTs. M8 needs a real backend — options:
   - Cloudflare Worker / Vercel Function (free tier; ed25519 verification in JS/Rust)
   - Lightweight FastAPI service on a $5/mo VPS
   - GitHub-flavored: PRs to a `telemetry/submissions/` directory with CI auto-merge for valid signatures (no backend; trades latency for simplicity)
   - **Decision deferred until M8 — until then telemetric scores are a TODO field.**

4. **Browse pane placement.** ADR-0055 leaves this as "design call deferred to implementation." Choices:
   - New top-level tab `Marketplace` between `Tools` and `Memory`
   - Subsection of the `Tools` tab (less prominent; easier discovery for tool-focused operators)
   - Modal launched from the existing `+ New tool` button on Tools tab (most contextual)
   - **Recommendation: new top-level tab. Browsing is a distinct activity from forging.**

5. **Reviewer trust model bootstrap.** D8 specifies signed reviews with optional "verified reviewer" tier. Day-zero question: any signed review is initially trustless to operators who haven't configured reviewer keys. Decision: default to "show all signed reviews with no trust badge"; verified-reviewer onboarding happens organically as the marketplace ages.

---

## Phase A — Working substrate (kernel + minimal sibling repo)

Goal: an operator can browse and install ONE plugin from a real registry. No reviews, no telemetry, no templates yet. Just the loop closes.

| # | Tranche | Where | Effort | Depends on |
|---|---|---|---|---|
| A1 | M2: `forest-marketplace` repo scaffold (README, CONTRIBUTING, registry/marketplace.yaml v0.1 with `soulux-computer-control` as the first entry, manifest-schema.md, signing tool stub) | sibling | 0.5 day | Open Decision 1 (repo owner) |
| A2 | M1: kernel `GET /marketplace/index` endpoint + settings env vars (`FSF_MARKETPLACE_REGISTRIES`, `FSF_MARKETPLACE_TRUSTED_KEYS`, `FSF_MARKETPLACE_CACHE_TTL_S`). Local-only fixture registry tests via `file://` URL. | kernel | 1 burst | A1 |
| A3 | M3: kernel `POST /marketplace/install` endpoint that downloads `.plugin`, verifies SHA256, delegates to existing `/plugins/install`. New `marketplace_plugin_installed` audit event. | kernel | 1 burst | A2 |
| A4 | M6: ed25519 signing pipeline — kernel-side signature verify against `FSF_MARKETPLACE_TRUSTED_KEYS`. Sibling-repo `tools/sign-manifest.py`. | both | 1 burst | Open Decision 2 (keypair) |
| A5 | M4: frontend Browse pane on new `Marketplace` tab (per Open Decision 4). Lists entries from `/marketplace/index`, filter by `highest_side_effect_tier` + `archetype_tags`, install button. | kernel | 1.5 bursts | A2, A3 |
| A6 | M5: post-install "Use with [agent]" flow that calls the existing `POST /agents/{id}/plugins/grant` with `trust_tier` derived from `highest_side_effect_tier` per ADR-0055 D7. | kernel | 0.5 burst | A5, ADR-0060 (done) |

**Phase A deliverable:** opening Forest, clicking Marketplace tab, picking `soulux-computer-control`, clicking Install → daemon downloads, verifies signature + SHA, registers the plugin, hot-reloads, offers grant-to-agent dropdown. Operator clicks an agent → grant fires through the existing pane from B223.

**Phase A bursts:** ~5 kernel bursts + scaffold the sibling repo. **2-3 days of focused work.**

## Phase B — Community signal (reviews + auditability)

Goal: operators can see what other operators think of a plugin, can pin to specific registry commits, can see staleness signals.

| # | Tranche | Where | Effort | Depends on |
|---|---|---|---|---|
| B1 | M7 part 1: review YAML schema in sibling repo + signing tool + CI verification | sibling | 1 day | A1, A4 (signing infra) |
| B2 | M7 part 2: kernel `/marketplace/index` aggregates `review_count` + `star_average` per entry. Lazy `GET /marketplace/reviews/<entry-id>` for full bodies. `FSF_MARKETPLACE_REVIEWS_ENABLED` toggle. | kernel | 1 burst | B1 |
| B3 | M7 part 3: frontend renders stars + review preview + "show all reviews" panel | kernel | 1 burst | B2, A5 |
| B4 | M10 part 1: per-source commit pinning support in kernel (`FSF_MARKETPLACE_REGISTRIES` accepts `@sha` syntax) | kernel | 0.5 burst | A2 |
| B5 | M10 part 2: change-log surfacing in browse pane (links to sibling-repo git history for each entry's manifest files) | kernel | 0.5 burst | B4 |
| B6 | M10 part 3: review-staleness flagging (review-for-vN shows badge when entry advances to vN+1) | kernel | 0.5 burst | B2 |

**Phase B deliverable:** operator can compare two MCP plugins side-by-side via stars, read curated reviews, see the maintainer's last update, see whether reviews are stale for the current version, and pin to a specific registry commit for reproducibility.

**Phase B bursts:** ~4 kernel bursts + 1 day of sibling-repo work. **3-4 days of focused work.**

## Phase C — Telemetric scores

Goal: skill quality is measured via opt-in operator telemetry, surfaced as an objective signal alongside subjective stars.

| # | Tranche | Where | Effort | Depends on |
|---|---|---|---|---|
| C1 | Open Decision 3: pick a telemetry endpoint host (Cloudflare Worker recommended for free + signed-payload verification). | external | 1 day | — |
| C2 | M8 part 1: daemon-side telemetry batcher behind `FSF_TELEMETRY_REPORT=true`. Reads chain `skill_completed` + `skill_step_failed` events per skill_hash, batches per week, ed25519-signs, POSTs to the configured endpoint. | kernel | 1.5 bursts | C1 |
| C3 | M8 part 2: `fsf telemetry preview` CLI that shows the operator exactly what gets sent (skill_hash, step counts, success tallies — NO content, NO identities). | kernel | 0.5 burst | C2 |
| C4 | M8 part 3: sibling-repo aggregator + statistical-anomaly rejection + score republication into entries | sibling | 2 days | C1 |
| C5 | M8 part 4: frontend renders telemetric score alongside star rating with "community-reported, not Forest-verified" label per ADR-0055 D9 | kernel | 0.5 burst | C4, B3 |

**Phase C deliverable:** every installed skill carries both a subjective star rating (from B3) and an objective success-rate score (this phase). Operators see which skills actually work in the field.

**Phase C bursts:** ~3 kernel bursts + 2 sibling-repo days + telemetry endpoint stand-up. **5-6 days of focused work.**

## Phase D — Agent templates

Goal: agents are first-class marketplace items. Operators clone-and-modify existing agents or instantiate from registry templates.

| # | Tranche | Where | Effort | Depends on |
|---|---|---|---|---|
| D1 | M9 part 1: `.template` package shape in sibling repo (template.yaml + soul.md.j2 + constitution.yaml.j2 + recommended_grants.yaml) + first template (`operator-companion-starter` based on the current operator_companion archetype) | sibling | 1 day | A1 |
| D2 | M9 part 2: kernel `agent_birthed_from_template` audit event registered in KNOWN_EVENT_TYPES | kernel | 0.5 burst | — |
| D3 | M9 part 3: marketplace template browse pane (sibling to plugin browse) + edit form rendering with Jinja-substituted defaults | kernel | 1.5 bursts | A5, B3 |
| D4 | M9 part 4: post-instantiate "also grant these tools?" follow-up that walks the `recommended_grants.yaml` items through the existing ADR-0060 grant endpoint | kernel | 1 burst | ADR-0060 (done) |
| D5 | M9 part 5: "Clone this agent" action on the Agents tab that templatizes an alive agent's artifacts locally (no roundtrip to marketplace) | kernel | 1 burst | D3 |

**Phase D deliverable:** new operators can stand up a working assistant in 3 clicks (browse → pick template → birth). Experienced operators can clone their best operator_companion and tweak the trait sliders for a German-language variant in one panel.

**Phase D bursts:** ~4 kernel bursts + 1 sibling-repo day. **4-5 days of focused work.**

---

## Critical path

```
A1 → A2 → A3 → A5 → A6                   (working install loop)
       ↘
        A4 (signing, parallel to A5/A6)
                ↘
                 B1 → B2 → B3            (reviews)
                              ↘
                               B5 ← B4   (commit pinning + change log)
                                  ↘
                                   B6    (review staleness)

C1 (external) → C2 → C3                  (telemetry, can run parallel to B)
              ↘ C4 → C5                  (after C1)

D1 → D2 → D3 → D4 → D5                   (templates, can run parallel to C)
```

**Phase A is the gate.** Everything else builds on its substrate. Phase B + C + D can run in parallel once Phase A lands.

## Bursts inventory

| Phase | Kernel bursts | Sibling-repo days | External setup |
|---|---|---|---|
| A | 5 | 0.5 | maintainer key (M6) |
| B | 4 | 1 | — |
| C | 3 | 2 | telemetry endpoint (C1) |
| D | 4 | 1 | — |
| **Total** | **~16 bursts** | **~4.5 days** | **2 external pieces** |

At the session pace observed today (18 bursts in one session), the kernel work is plausibly one focused week. The sibling-repo work + external infra setup is the longer pole.

## What we deliberately defer

- **Marketplace UI rich-media** (screenshots, demo videos, capability matrices). MVP renders plain text + capability list. Visual polish is post-Phase-D.
- **In-kernel review submission UI.** Reviews are submitted via PR to the sibling repo (CI verifies signatures + appends). The kernel only renders; submission stays out-of-band to keep the kernel slim.
- **Multi-registry federation.** ADR-0055 D2 specifies decentralized-with-curated-default; Phase A ships with one default registry. Adding second/third registry URLs works (the `FSF_MARKETPLACE_REGISTRIES` env var already accepts a list) but multi-registry conflict resolution (same entry name in two registries) is post-MVP.
- **Plugin uninstall via marketplace UI.** Today operators use `fsf plugin uninstall` (existing CLI) or the new B212 DELETE endpoints. The marketplace UI shows installed-status badges but doesn't add a new uninstall button — uninstall is already covered.

## Risks

1. **Sibling-repo bottleneck.** The marketplace is a content/policy layer; its value comes from the breadth of entries. Phase A's deliverable with one entry isn't compelling on its own — needs 5-10 plugins in the registry to feel real. Sourcing those entries is community work, not kernel work.

2. **Telemetry adoption.** C2 is opt-in (`FSF_TELEMETRY_REPORT=true`, default false). If most operators stay opted-out, the telemetric score signal is weak. Mitigation: ship with an explicit telemetry-preview UI that surfaces what would be sent so operators can opt in with eyes open.

3. **Review gaming.** D8 anti-sock-puppet relies on one-key-one-review. A determined attacker can generate keys; the marketplace's CI is the chokepoint. M7 part 1 must implement rate-limiting + statistical anomaly checks before reviews matter as signal.

4. **Template DNA boundary confusion.** D10 makes new DNA per instantiation, but "Clone this agent" creates the perception that lineage transfers. UX copy must be explicit: "this is a new agent born from a template based on X; it does NOT inherit X's memory, lineage, or audit history."

## Where to start

Phase A1 (`forest-marketplace` repo scaffold) needs Open Decision 1 resolved before it can ship. Other than that, Phase A is unblocked.

Recommended first burst when the marketplace track starts: scaffold the sibling repo with one entry (`soulux-computer-control` is already plugin-shaped from B202 — perfect first registry resident). Then kernel-side M1 (the index endpoint) follows in the next burst.

**Open Decision 1 is the unblocking action.** Once the sibling repo exists, ~16 kernel bursts + ~4.5 sibling-repo days produce a marketplace that does everything ADR-0055 D1-D11 describes.
