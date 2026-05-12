# ADR-0055 — Agentic Marketplace

**Status:** Proposed (2026-05-06, expanded 2026-05-11 with
Decisions 8–11 + Tranches M7–M10). **Phase A shipped 2026-05-11
(Bursts 227–229) + 20-item seed catalog 2026-05-11 to 2026-05-12
(Bursts 230–233)** per the phased rollout in
`docs/roadmap/2026-05-11-marketplace-roadmap.md`. Phases B/C/D
queued. Sibling-repo design — most delivery lives outside
Forest-Soul-Forge in a new `forest-marketplace` repo (org not yet
scaffolded as of 2026-05-12). Kernel-side scope is intentionally
narrow: `GET /marketplace/index` (M1, shipped B184), `POST
/marketplace/install` (M3, shipped B227 with tarball-traversal
defense), `GET /marketplace/reviews/<entry-id>` (M7, Phase B),
and three new audit event types (`marketplace_plugin_installed`
— live since B227; `agent_birthed_from_template` — Phase D;
`marketplace_telemetry_submitted` — Phase C). Pairs with ADR-0043
(MCP plugin protocol), ADR-0030/0031 (forge lifecycle), ADR-0060
(runtime tool grants — marketplace install flows into the grant
pane via the B229 post-install grant-to-agent picker), and
ADR-0001 (identity invariance — marketplace installs MUST NOT
touch constitution_hash or DNA; agent templates produce NEW DNA,
not transplanted DNA).

## Context

The plug-and-play substrate is mostly built. ADR-0043 ships
`.plugin` packages with manifests; ADR-0030/0031 emit
`forge_tool_proposed` / `forge_tool_installed` audit events
for the install lifecycle; ADR-0052 gives plugins a credentials
store; ADR-0048's `soulux-computer-control` is a working
example of a plugin contributing tools at lifespan.

What's missing is the discovery and distribution layer
operators see when they open the daemon UI:

- No browse surface inside the daemon. Plugins ship as
  `.plugin` files via file-share, git, or word-of-mouth.
- No central index of what plugins exist, who maintains them,
  what versions, what capabilities.
- No capability search. Operator can't ask "show me skills
  that read filesystems" or "tools fit for a Companion-genre
  agent."
- No recommendation. "Agents of role X typically install
  these plugins" surface doesn't exist.
- No one-click install + grant-to-agent flow. Today: shell
  command, then constitutional edit, then re-birth or
  `tools_add`. Wants to be one button.
- No trust chain. Plugin manifests SHA256-verify against
  themselves but aren't signed by a marketplace authority,
  so a community contribution looks identical to a typo-
  squat.

The 2026-05-06 operator directive frames the target shape:

> i want skills and tools to be loaded like the matrix sort
> of, prebuilt programs that give plug and play access via
> a agentic marketplace

Read literally — Trinity downloads helicopter piloting in
seconds. Read pragmatically — the operator wants the
Forest equivalent of `cargo install` + `npm browse` + a
trustable reputation channel, organized around the
existing plugin substrate.

## Decision

Build a **sibling repo** (`forest-marketplace`) that
publishes a registry of available plugins with a
machine-readable manifest, a curated catalog, signing
tools for community contributors, and a frontend Browse
pane in the kernel that talks to it. The kernel itself
exposes one new read endpoint and reuses the existing
`POST /plugins/install` for the install side. All
governance — trust tier, signing, capability indexing,
ratings — lives in the sibling repo so the kernel stays
slim and the marketplace can ship faster than the
governance core's release cadence.

### Decision 1 — Sibling repo, not in-Forest

The marketplace concerns are independently versioned.
`forest-marketplace` ships its registry on its own cadence;
the kernel only needs the API contract to stay stable.

Layout:

```
forest-marketplace/
├── README.md
├── CONTRIBUTING.md            # how to submit a plugin
├── registry/
│   ├── marketplace.yaml       # canonical index (this is what
│   │                          # the kernel fetches)
│   └── entries/               # per-plugin manifests
│       ├── soulux-computer-control.yaml
│       ├── ...
├── tools/
│   ├── sign-manifest.py       # signing tool for maintainers
│   ├── verify-manifest.py     # operator-side verification
│   └── add-entry.py           # contributor onboarding helper
├── docs/
│   ├── manifest-schema.md
│   ├── trust-model.md
│   └── submission-guide.md
└── tests/
    └── test_registry_validity.py
```

Why a 7th sibling alongside `forest-blue-team-guardian`,
`forest-collector`, `buddy`, `cus-core`, `CompanionForge`,
`MouseMates` (per the Forest project family memory): keeps
the marketplace policy decisions independently auditable.
The kernel doesn't need to re-release when a new plugin
lands or when a maintainer key rotates.

### Decision 2 — Registry shape: decentralized with a curated default

Operators pin a list of registry URLs. The default list ships
exactly one entry: the official `forest-marketplace` registry
(initially served via raw GitHub URL; eventually a CDN-backed
endpoint). Operators can add community registries by appending
URLs to `FSF_MARKETPLACE_REGISTRIES` or via the settings UI.

Trade-off: matches the Cargo / npm / crates.io convergence on
"central by default, decentralized by escape hatch." Centralized
discovery + decentralized hosting. The kernel doesn't HTTP-fetch
plugin payloads — only manifest indexes. Payload downloads use
URLs from the manifest, which can point anywhere (GitHub
release, S3, whatever the maintainer hosts).

### Decision 3 — Marketplace manifest schema

Each entry in `registry/marketplace.yaml`:

```yaml
- id: soulux-computer-control
  name: Soulux Computer Control
  version: "1.0.0"
  author: forest-team
  source_url: https://github.com/StellarRequiem/soulux-computer-control
  download_url: https://github.com/StellarRequiem/soulux-computer-control/releases/download/v1.0.0/soulux-computer-control.plugin
  download_sha256: <hex>
  manifest_signature: <ed25519 signature over the canonical
                       JSON of this entry, signed by the
                       registry maintainer's key — see
                       trust-model.md>
  description: |
    Drive the operator's macOS desktop via Anthropic's
    computer-use protocol. Read clipboard, click, type,
    launch URLs.
  contributes:
    tools:
      - name: computer_screenshot
        version: "1"
        side_effects: read_only
      - name: computer_click
        version: "1"
        side_effects: external
      - name: computer_type
        version: "1"
        side_effects: external
      # ...
    skills: []
    mcp_servers: []
  archetype_tags: [companion, assistant]
  highest_side_effect_tier: external   # operator-facing
                                       # filter: "show me
                                       # only read_only
                                       # plugins"
  required_secrets: []
  minimum_kernel_version: "v0.6"
  permissions_summary: |
    This plugin can SEE your screen and CONTROL your mouse
    and keyboard. Grant only to agents you trust to act on
    your behalf. Default posture clamps tighten this — see
    ADR-0048.
  reviewed_by:
    - reviewer: forest-team
      date: 2026-05-04
      verdict: approved
      audit_url: https://forest-marketplace/audits/...
```

The `permissions_summary` is mandatory and operator-readable —
the marketplace UI surfaces it as the plain-language version of
the `contributes` capabilities. No "this plugin needs
filesystem access — Allow?" UI without a sentence explaining
WHY.

### Decision 4 — Kernel API surface (minimal additions)

The kernel exposes:

- **`GET /marketplace/index`** — Aggregates entries from every
  configured registry, returns one merged list with a
  `source_registry` field per entry so the operator can see
  which registry contributed each. Caches per-registry for
  `FSF_MARKETPLACE_CACHE_TTL_S` (default 3600). On cache miss
  + network failure, returns last-known-good with a
  `stale: true` marker. Read-only; no auth required beyond
  the standard FSF_API_TOKEN.

- **`POST /marketplace/install`** — Takes `{registry_id,
  entry_id, version}`, downloads the `.plugin` from
  `download_url`, verifies SHA256 against `download_sha256`,
  verifies `manifest_signature` against the configured
  trusted-keys list, then delegates to the existing
  ADR-0043 `POST /plugins/install` handler. Emits
  `forge_tool_proposed` (existing event) THEN, on success,
  a NEW event `marketplace_plugin_installed` with the
  registry source + version + signature digest so the
  audit chain captures provenance.

These two endpoints are the kernel's only marketplace-aware
code. Everything else (UI, registry schema, signing) lives in
`forest-marketplace`. The existing
`POST /agents/{instance_id}/plugins/grant` endpoint
(ADR-0043) is reused as-is for the grant-to-agent flow —
no marketplace-specific changes needed.

### Decision 5 — Trust + signing model

Three trust layers, evaluated in order:

1. **Manifest-level signature.** Each marketplace entry is
   signed by the registry maintainer's ed25519 key. The kernel
   verifies the signature on read. Operators configure trusted
   keys via `FSF_MARKETPLACE_TRUSTED_KEYS` — defaults to one
   key for the official Forest registry. Unsigned or
   wrong-signature entries appear in the browse pane with a
   prominent untrusted badge; install requires explicit
   operator confirmation.

2. **Payload SHA256.** The manifest's `download_sha256`
   pins the bytes of the `.plugin` file. Mismatch on
   download = abort install. Same SHA-pinning ADR-0043
   already requires for plugin manifests; this lifts it to
   the registry layer.

3. **Plugin-internal manifest.** Once downloaded, the
   `.plugin`'s own internal manifest goes through the
   existing ADR-0043 verification (declared tools/skills
   actually exist in the package, side_effects match the
   installed tool, etc.).

Operator can downgrade any of these per-entry — e.g., trust
an unsigned community entry by pinning the entry ID — but
the default posture rejects unsigned entries at install time.

A separate `forest-marketplace/docs/trust-model.md` covers
the maintainer key rotation procedure, what to do when a key
is compromised, and how the operator should respond to a
revocation event.

### Decision 6 — Capability search + role-fit recommendation

The kernel's `GET /marketplace/index` returns the merged list.
The frontend computes search/recommendation client-side
because:

- The list is bounded (low hundreds of plugins for years).
- Operator-facing filters (side_effects tier, archetype_tags,
  has_tools_named_X) are all simple predicate matches over
  the indexed fields — no reason to round-trip to the daemon.
- Kernel stays slim. No FTS index to maintain.

Recommendation v0.1 is rule-based: show entries whose
`archetype_tags` overlap with the agent's role's archetype
tags first, sorted by `highest_side_effect_tier` ascending
(safer first). v0.2 may layer operator install-history
weighting if usage patterns warrant it.

### Decision 7 — Grant-to-agent flow

After install, the marketplace UI offers "Use with
\[agent picker\]." Selecting an agent calls the existing
`POST /agents/{instance_id}/plugins/grant` (ADR-0043
follow-up #2 / Burst 113) with `trust_tier` derived from
the entry's `highest_side_effect_tier`:

| highest_side_effect_tier | default trust_tier |
|:---|:---|
| `read_only`   | green  |
| `network`     | green  |
| `filesystem`  | yellow |
| `external`    | yellow |

The operator can override the default with the existing
ADR-0045 trust-tier picker. This makes a one-click "install
+ grant green-tier to the assistant" flow, which is the
common case for read-only utility plugins.

### Decision 8 — Reviews + star ratings (2026-05-11 expansion)

The marketplace publishes per-entry reviews + numeric ratings so
operators can rely on community signal, not just maintainer claims.

**Storage shape:** reviews live in the sibling repo as one signed
file per review under `registry/reviews/<entry-id>/<review-id>.yaml`.
Each review carries:

```yaml
schema_version: 1
entry_id: soulux-computer-control
reviewer_pubkey: <ed25519 pubkey hex>
reviewer_handle: stellarrequiem      # operator-visible name
stars: 4                              # 1-5 integer
verdict: approved                     # approved | flagged | broken
body: |
  Worked end-to-end on macOS 26 with no setup beyond the
  posture clamp. Saved me an hour on the IR triage skill.
attestations:
  - "I have actually installed and used this plugin."
  - "I am not affiliated with the maintainer."
reviewed_at: 2026-05-11T14:23:00Z
signature: <ed25519 signature over canonical JSON above>
```

**Trust model:**

- Reviews are signed by a reviewer key. The kernel verifies the
  signature on read; unsigned reviews don't display.
- Operators configure trusted reviewer keys via
  `FSF_MARKETPLACE_TRUSTED_REVIEWERS` (default: empty — show all
  signed reviews regardless of trust list).
- A separate trust tier ("verified reviewer") flags maintainers
  who have demonstrated good-faith review activity. The
  marketplace repo's policy controls this list; the kernel just
  renders the badge.
- Anti-sock-puppet: each reviewer pubkey can submit ONE review
  per entry. Re-reviews replace the prior (the marketplace's CI
  enforces this); the marketplace's own git history shows the
  edit lineage.

**Aggregation:** the kernel's `GET /marketplace/index` response
gains per-entry `review_count` + `star_average` (computed
server-side from the registry's review files). The full review
text is fetched lazily via `GET /marketplace/reviews/<entry-id>`
when the operator clicks through.

**Off by default for anonymous installs:** operators can
disable review-display entirely via
`FSF_MARKETPLACE_REVIEWS_ENABLED=false` for environments where
community signal is undesirable (air-gapped, regulated).

### Decision 9 — Skill scoring (subjective + telemetric)

Skills behave differently from tools — they're orchestrations of
multiple tool calls, and their "quality" is measured by both
operator opinion AND runtime telemetry.

**Two score dimensions, both stored in the marketplace entry:**

1. **Star rating** (subjective, same as tools per D8).
2. **Telemetric score** (auto-computed): a 0-100 number derived
   from chain-emitted `skill_completed` / `skill_step_failed`
   events for that skill. Surfaced as "running success rate
   across N operators" alongside the star rating.

**Telemetric flow:**

```
operator opt-in (FSF_TELEMETRY_REPORT=true, default false)
  → daemon batches skill_completed + skill_step_failed events
    per skill_hash, per week
  → POST to forest-marketplace's /telemetry/submit endpoint
    with ed25519-signed batch (operator's daemon key)
  → marketplace verifies + aggregates + republishes
  → next /marketplace/index refresh exposes the new score
```

**Privacy:** telemetry batches carry skill_hash + step counts +
success/failure tallies. NO conversation content, NO agent
identities, NO operator identifiers (only the daemon's
random-per-machine reporting key). Operator can audit what gets
sent via `fsf telemetry preview`.

**Trust:** sock-puppet daemons inflating success rates are
mitigated by (a) per-key submission rate-limits, (b) statistical
anomaly detection in the aggregator, (c) the score being
explicitly labeled "community-reported, not Forest-verified."

### Decision 10 — Agent templates (clone + modify workflow)

Agents are first-class marketplace items. An "agent template" is
a `.template` package containing:

```
soul-summarizer-v1.template/
├── template.yaml           # marketplace metadata + role + traits
├── soul.md.j2              # Jinja-rendered narrative starter
├── constitution.yaml.j2    # constitution skeleton (tools_add list)
├── recommended_grants.yaml # ADR-0060 tool grants to issue on instantiate
└── README.md
```

**Workflow:**

1. Operator browses marketplace's "Agents" section.
2. Clicks "Use template" on `soul-summarizer-v1`.
3. Frontend renders the editable form pre-filled from the
   template (role, agent_name, trait_values, tools_add).
4. Operator tweaks (or accepts defaults) and clicks Birth.
5. Daemon's existing `/birth` endpoint handles it. Audit event
   `agent_birthed_from_template` (NEW) records the template id +
   version + render-time variables so an auditor can reproduce
   the exact birth.
6. Recommended runtime grants from `recommended_grants.yaml`
   are presented as an "also grant these tools?" follow-up
   (operator opts in per-grant, no autograb).

**Cloning:** "Clone this agent" is a sibling action that takes
an EXISTING alive agent's soul.md + constitution.yaml as the
template source, renders a `.template` package locally, and
opens the same edit form. Closes the "I want one just like
Operator_Companion but for German" loop without a marketplace
roundtrip.

**Identity boundary:** templates produce NEW DNA + new
`constitution_hash`. Cloning doesn't transplant memory or
lineage — those stay with the source agent. This preserves the
ADR-0001 invariant that DNA is per-agent, not per-template.

### Decision 11 — Marketplace auditability

The marketplace registry is a Git repository — Git's commit
chain already gives us a tamper-evident history. ADR-0055
formalizes the kernel's verification:

1. **Pinned commit per registry source.** Operators can pin
   `FSF_MARKETPLACE_REGISTRIES` to a specific commit
   (`https://github.com/forest-org/forest-marketplace@abc1234`).
   When pinned, the kernel only fetches at that commit; rolling
   forward is an explicit operator action.

2. **Per-entry change log.** The marketplace surfaces each
   entry's `last_modified_at` + `last_modified_by` (signed
   commit author) in the browse pane. An operator inspecting a
   plugin can see the full edit history without leaving the
   kernel UI (link out to the marketplace repo's `git log` for
   that entry's files).

3. **Review-chain replay.** Each review file is content-
   addressed; a review's signature covers its full canonical
   JSON including the entry-version it was written against.
   When an entry updates, prior reviews flag with "review was
   for vN-1, this is vN" so operators see stale signal
   explicitly.

4. **Marketplace audit-event mirror.** Significant marketplace
   actions (entry added, entry deprecated, key revoked,
   reviewer trust changed) emit signed announcements that the
   kernel can subscribe to. These don't go in the agent audit
   chain — they're per-machine notifications, surfaced in
   the SoulUX status bar — so an operator sees "the plugin you
   installed yesterday was deprecated this morning" without
   polling.

This decision doesn't change kernel storage (the agent audit
chain stays orthogonal to marketplace state) but formalizes the
trust path so an auditor reviewing "how did this tool get on
this agent?" can trace from the kernel's
`marketplace_plugin_installed` event → registry commit →
manifest signature → maintainer key → reviewer attestations.

## Implementation Tranches

- **M1** — kernel `GET /marketplace/index` endpoint that
  aggregates configured registries; tests; settings env vars
  (`FSF_MARKETPLACE_REGISTRIES`,
  `FSF_MARKETPLACE_TRUSTED_KEYS`,
  `FSF_MARKETPLACE_CACHE_TTL_S`). Local-only fixture
  registry first (file:// URL pointing at a tmp-path
  marketplace.yaml) so tests don't hit the network.
- **M2** — `forest-marketplace` sibling repo scaffold:
  README, CONTRIBUTING, registry/marketplace.yaml v0.1
  listing soulux-computer-control + planned community
  stubs, manifest-schema.md, signing-tool scaffolding
  (no actual signing yet — that's M6). Test that the
  registry YAML validates against the schema.
- **M3** — kernel `POST /marketplace/install` endpoint:
  download + SHA verify + delegate to existing
  `POST /plugins/install`. New `marketplace_plugin_installed`
  audit event registered in `KNOWN_EVENT_TYPES`.
- **M4** — frontend Marketplace pane (chat tab settings or
  a new top-level tab — design call deferred to
  implementation): browse list, capability filters, sort by
  side_effect tier, install button, links to source_url +
  permissions_summary.
- **M5** — grant-to-agent flow: after install, "use with"
  picker that calls the existing
  `POST /agents/{id}/plugins/grant` with auto-derived
  `trust_tier` per Decision 7.
- **M6** — ed25519 signing pipeline: signing tool in the
  sibling repo + verify in the kernel + frontend "untrusted"
  badge for unsigned entries + operator-confirmation
  override path.
- **M7** — operator ratings + reviews per Decision 8.
  Marketplace adds `registry/reviews/<entry>/<id>.yaml`
  schema + ed25519 signing tool + verify on read. Kernel
  adds `review_count` + `star_average` to
  `/marketplace/index` response and a lazy
  `GET /marketplace/reviews/<entry-id>` for full bodies.
  Feature flag `FSF_MARKETPLACE_REVIEWS_ENABLED` (default on).
- **M8** — telemetric skill scores per Decision 9. Daemon
  side: opt-in batched submission to the marketplace's
  `/telemetry/submit` endpoint behind
  `FSF_TELEMETRY_REPORT=true` (default false).
  Marketplace side: aggregator + statistical-anomaly
  rejection + republishing to entries.
  `fsf telemetry preview` operator CLI for transparency.
- **M9** — agent templates per Decision 10. New
  `.template` package shape. Marketplace gains a Templates
  section. Kernel gains `agent_birthed_from_template` audit
  event + the recommended-grants follow-up step. Clone-this-
  agent action lives entirely in the frontend (renders a
  local template from an alive agent's artifacts and opens
  the edit form).
- **M10** — marketplace auditability per Decision 11.
  Per-source commit pinning + change-log surfacing + review-
  staleness flagging + signed-announcement subscription
  channel. Pure UX layer over Git's existing tamper-evident
  history; no new kernel storage.

## Consequences

**Positive:**

- The substrate operators wanted (browse + one-click
  install + grant) ships incrementally without a kernel
  refactor — every tranche above is additive.
- Marketplace governance lives in its own repo, so plugin
  policy decisions don't gate kernel releases.
- Security posture improves: signing the manifest makes
  typo-squat attacks more expensive; SHA-pinning the
  payload prevents post-publish tampering.
- Operator gets a clear answer to "what can I install
  on this agent?" — capability filters surface the
  archetype-tag fit recommendation directly.

**Negative:**

- Distributed governance: when a maintainer key gets
  compromised, the recovery procedure crosses two repos
  (rotate in `forest-marketplace`; operators update
  `FSF_MARKETPLACE_TRUSTED_KEYS`). M6 documents this
  carefully.
- Discoverability is only as good as the curated registry.
  v0.1 ships with one entry (soulux-computer-control); the
  registry needs to grow for the marketplace to feel real.
- Network dependency. The browse pane needs to reach a
  registry URL; offline operators see the cached last-
  known-good or an empty pane. Acceptable for v0.1; M3+
  considers a "bundle the registry on-disk" fallback for
  fully air-gapped operators.

**ADR-0001 D2 invariance verification:** Marketplace
installs add NEW tools/skills/MCP servers to the
dispatcher's runtime registry. They do NOT touch any
existing agent's `constitution_hash` or DNA. An agent
gains access to a marketplace-installed plugin only via
the existing per-(agent, plugin) grant path
(ADR-0043 follow-up #2), which is per-instance state and
revocable. Identity invariance preserved.

**ADR-0044 D3 ABI verification:** Two new endpoints
(`GET /marketplace/index`, `POST /marketplace/install`)
and one new audit event type (`marketplace_plugin_installed`).
Both are additive. Pre-M3 daemons reading post-M3 chains
emit a verification warning on the new event type rather
than failing — same forward-compat posture as ADR-0054 T4
introduced for `tool_call_shortcut`.
