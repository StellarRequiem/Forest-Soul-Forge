# ADR-0055 — Agentic Marketplace

**Status:** Proposed (2026-05-06). Sibling-repo design — most
delivery lives outside Forest-Soul-Forge in a new
`forest-marketplace` repo. Kernel-side scope is limited to one
new endpoint (`GET /marketplace/index`) and one new audit
event type. Pairs with ADR-0043 (MCP plugin protocol),
ADR-0030/0031 (forge lifecycle events), and ADR-0001 (identity
invariance — marketplace installs MUST NOT touch
constitution_hash or DNA).

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
- **M7** — operator ratings + reviews. Deferred. Needs
  separate design conversation: anonymity, gaming, off-by-
  default.

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
