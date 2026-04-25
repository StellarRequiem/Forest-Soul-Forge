# ADR-0017 — LLM-enriched soul.md narrative

- **Status:** Proposed
- **Date:** 2026-04-25
- **Supersedes:** —
- **Related:** ADR-0004 (constitution builder), ADR-0006 (registry as index over artifacts), ADR-0007 (FastAPI daemon), ADR-0008 (local-first model provider). Phase 4 first slice (`POST /runtime/provider/generate`, commit `cfa6d3b`).

## Context

`SoulGenerator` (`src/forest_soul_forge/soul/generator.py`) currently produces a soul.md from a `TraitProfile` by deterministic template fill: YAML frontmatter (identity + lineage + trait_values + constitution_hash reference) followed by Markdown prose rendered from the trait engine's per-trait `scale_low / scale_mid / scale_high` strings. Two agents with similar profiles read substantially the same — the same trait band labels, the same boilerplate verbs, the same core-rules block verbatim. The deterministic prose was correct for Phases 1–3 (when nothing called an LLM and the body was a placeholder for "we will generate this later"), but it fails the product goal: agents in the Forge are supposed to feel distinct, with their own voice and decision-making cadence, and the templated body cannot deliver that.

The Phase 3 verification of the LLM stack (commits `0a39a6b`, `cfa6d3b`) and the new `POST /runtime/provider/generate` endpoint mean we now have the substrate to call a model from inside `/birth` and `/spawn`. The architectural question this ADR answers is: **how do we add LLM-generated content to soul.md without breaking the reproducibility contracts that ADRs 0002, 0004, and 0006 rely on?**

### What is currently hashed, and what is not

A read of `soul/generator.py`, `core/dna.py`, `core/constitution.py`, and `registry/ingest.py` establishes the relevant invariants:

- **`dna` / `dna_full`** are hashes of `profile.trait_values` (sorted, canonical encoding) and the role string. They live in soul.md frontmatter but are computed from the profile, not from the soul.md text.
- **`constitution_hash`** is the SHA-256 of the rendered `<agent>.constitution.yaml` file — a *sibling* artifact built by `core/constitution.py:build()` from the profile + engine. soul.md merely references it in frontmatter; the bytes that get hashed are in the constitution.yaml file, not soul.md.
- **soul.md frontmatter** has fields that already vary across generations of the same profile: `generated_at` (timestamp), and the registry-supplied `instance_id` / `sibling_index`. So "soul.md is bit-for-bit reproducible from the profile alone" was never the contract.
- **soul.md body** (the prose under the second `---` divider) is **not hashed by anything**. The ingest parser's own docstring states it explicitly: *"soul.md files — YAML frontmatter between two `---` fences, followed by generated prose. **We only need the frontmatter.**"* The body is read by humans, not by code.

This means: adding non-deterministic content to the soul.md body disturbs zero cryptographic invariants. The constitution_hash is unchanged because constitution.yaml is unchanged. The dna is unchanged because trait_values are unchanged. The audit chain is unchanged because the chain hashes events about the agent (its profile, its constitution_hash, its instance_id), not the soul.md body bytes. The registry is unchanged because ingest reads only the frontmatter.

Three shapes for adding LLM content were considered:

1. **Replace the entire body with LLM output.** Cleanest narrative; loses the templated trait prose that's useful for audit and skim-reading. If the model is unavailable, fallback content has to fully replace the LLM voice — a much wider gap to mind. **Rejected** as too aggressive for a first cut.

2. **Add a single LLM-generated "Voice" section, leave existing templated content in place.** The deterministic trait prose stays as a stable, verifiable readout of the profile; the LLM adds a bounded narrative section that captures the agent's voice. Failure mode is gentle (skip the section entirely; the soul still reads well). Auditors comparing two soul.md files for similar profiles can still compare the deterministic sections directly. **Selected.**

3. **Generate at birth, freeze in the registry, never re-render.** Solves "re-rendering the same profile yields different prose" but introduces a new persistence concern (soul body bytes in SQLite, not just on disk) that fights ADR-0006's "files-on-disk are canonical." **Rejected** — keeps the artifacts the source of truth and preserves the rebuild-from-artifacts path.

## Decision

soul.md body grows one new section, **`## Voice`**, written by the active model provider via `provider.complete()`. All existing body content (header, domain sections, core rules, profile warnings, lineage footer) stays exactly as it is. The Voice section is inserted between the header introduction paragraph and the first domain section.

Frontmatter gains three optional traceability fields:

```yaml
narrative_provider: "local"           # or "frontier" or "template"
narrative_model: "llama3.2:1b"        # resolved model tag, or "template" when fallback
narrative_generated_at: "2026-04-25 01:14:08Z"  # ISO timestamp of the LLM call
```

These are **purely informational** — not in any hash, not asserted by any contract. They exist so an auditor reading a committed soul.md can answer "what wrote the Voice paragraph?" without spelunking through the audit chain.

### Generation contract

`SoulGenerator.generate()` gains an optional `voice_renderer` callable. When supplied, the renderer is invoked with `(profile, role, dna, lineage)` and is expected to return either a `VoiceText` (markdown string + provider name + model tag + iso timestamp) or raise `ProviderError` / `ProviderUnavailable` / `ProviderDisabled`. Any exception from the renderer is caught at the soul-generator level and produces a templated fallback Voice block — identical wording as today's intro paragraph would have given. The frontmatter then records `narrative_provider: "template"`. Soul generation never fails because Ollama is down.

The renderer itself is implemented in the daemon's `routers/writes.py` (or a small helper in `soul/`) and built around `provider.complete()`. The system prompt asks for 2–4 short paragraphs about how this specific agent speaks, decides, and handles uncertainty, given the profile. The user prompt embeds the role description, the dominant domain(s), and the highest- and lowest-band traits in plain language. Bounded `max_tokens` (suggested 400) keeps the response focused and protects against runaway costs on a frontier provider.

### Public-API surface

`BirthRequest` and `SpawnRequest` (`daemon/schemas.py`) gain one optional field:

```python
enrich_narrative: bool = Field(
    default=True,
    description="When true (default), generate the soul.md Voice section via the active provider; on provider failure, fall back to a template. When false, always use the template — useful for tests and reproducible benchmarks.",
)
```

When `enrich_narrative=false`, the renderer is bypassed entirely and `narrative_provider: "template"` is recorded. This gives test suites a stable, deterministic mode without having to mock the provider at every call site.

### Idempotency interaction

The existing `X-Idempotency-Key` layer (Phase 3) caches the entire HTTP response by key. A retry with the same key returns the same response bytes verbatim, so the second request never re-invokes the LLM. The first invocation is the only time the model is called for a given idempotency key — already the right behavior; no additional logic needed.

### Auth interaction

`/birth` and `/spawn` already gate on `require_api_token`. Calling the LLM internally during a write doesn't widen the auth surface — the same token still gates the entire operation.

### Audit trail

The existing `agent_birthed` and `agent_spawned` event payloads gain three optional fields mirroring the frontmatter (`narrative_provider`, `narrative_model`, `narrative_generated_at`). Old events without these fields parse cleanly under the existing tolerant ingest. The audit chain hash remains over the event JSON as it was — adding optional fields is forward-compatible because consumers of `event_data` access fields by key. **No registry schema bump.**

### What an existing soul.md looks like vs. an enriched one

```markdown
---
schema_version: 1
dna: a3f2c1...
[...all existing fields unchanged...]
narrative_provider: "local"
narrative_model: "llama3.2:1b"
narrative_generated_at: "2026-04-25 01:14:08Z"
---

# Soul Definition — Huntmaster-01 v1

**Role:** `network_watcher` — [...unchanged...]
**Generated:** 2026-04-25 01:14:08Z _(auto-generated; do not hand-edit)_

You are the **Huntmaster-01** agent. [...existing intro paragraph unchanged...]

## Voice

[2–4 LLM-generated paragraphs in the agent's voice — how it speaks,
how it weighs evidence, how it handles uncertainty. Generated once at
birth; not re-rendered on rebuild-from-artifacts.]

## Network Awareness — dominant (weight 1.9)
[...all existing domain sections unchanged...]

## Core rules (non-negotiable)
[...existing block unchanged...]
```

Re-rendering a soul.md from the registry (the rebuild-from-artifacts path) reads the existing soul file's body as authoritative — it does not re-call the LLM. So a `git checkout` of an old soul.md gives you exactly that day's Voice content, byte-for-byte.

## Consequences

**Upside:**

- Agents in the Forge gain a distinct voice without sacrificing the deterministic, audit-friendly trait readout. Two agents with similar profiles can still be compared on the structured sections; the Voice section captures what the templated body fundamentally couldn't.
- The provider stack (LocalProvider, FrontierProvider, registry, healthcheck, switching) gains a second consumer in product features (after Phase 4's `/runtime/provider/generate`). The local-first guarantee from ADR-0008 transfers cleanly: by default, the Voice paragraph is written by your local Ollama, never leaving the machine.
- Failure modes are gentle. Ollama down → templated Voice block, soul.md still produced, `narrative_provider: "template"` recorded for transparency. No /birth call has ever failed because of a model-server problem under this design.
- Test infrastructure gets a clean opt-out (`enrich_narrative: false`) instead of mock-the-world plumbing for every test that exercises a write endpoint. Existing tests that already depend on deterministic soul.md output (snapshot-style asserts) stay green by passing `enrich_narrative=False` once.
- Provenance is visible in the artifact itself. An auditor doesn't have to consult the audit chain to answer "what wrote this paragraph?" — the frontmatter records the provider + model tag + timestamp.

**Downside:**

- Re-generating soul.md from the same profile produces different Voice content. (The deterministic sections still match.) This was implicitly true for `generated_at` already, but the Voice section makes the variance more visible. We document it explicitly rather than fight it.
- Birth latency goes up by one model round-trip when the provider is local (typically 1–4 seconds for an 8B model on consumer hardware producing 400 tokens). Acceptable for an interactive birth flow; would need to be revisited if /birth ever runs on a hot path.
- Adding a new section to soul.md is a soft-breaking change for consumers that parse the body. None of our consumers parse the body today (ingest only reads frontmatter), so the impact is currently zero, but downstream tooling that does spring up will see new content. The Voice section's heading (`## Voice`) is stable and distinctive, so consumers can detect it.
- A frontier-provider operator who flips `FSF_DEFAULT_PROVIDER=frontier` and runs /birth has every birth send the trait profile to a hosted API. ADR-0008's "two deliberate acts to enable frontier" framing covers this, and `narrative_provider: "frontier"` in the artifact makes the disclosure visible after the fact, but it's worth surfacing here.

**Out of scope for this ADR (deliberately):**

- Re-generating the Voice on demand (e.g., a hypothetical `POST /agents/{id}/regenerate-narrative`). Not needed for v1; the Voice is "what the agent said when it woke up."
- Multi-pass generation (draft + critique + refine). One-shot is enough for the first cut; we revisit if voice quality is consistently poor.
- Streaming the response back to the client. Birth is request/response; the wait is acceptable. Streaming would matter if /birth ever powered an interactive UI flow, which it doesn't today.
- A separate prompt-version field. Worth adding if we change the system prompt enough that old Voice content reads visibly different from new content. Defer until that happens.
- Schema-version bump on soul.md frontmatter. The new fields are optional; old soul files (without them) round-trip through ingest unchanged. We bump if we ever make a mandatory field change, not for additive optionals.

## Resolved questions

1. **Where does the renderer live?** A new `soul/voice_renderer.py` takes a provider and a profile and returns `VoiceText`, called from the daemon's writes router. Rationale: the system prompt is a product decision, not a provider-implementation detail; keeping it in `soul/` ties it to soul-generation concerns rather than provider plumbing.

2. **What `task_kind` does the LLM call use?** **Default `GENERATE`, operator-overridable via `FSF_NARRATIVE_TASK_KIND` env var.** Rationale: `GENERATE` matches the one-shot structured-generation intent and gives operators a clean routing lever (the model behind `GENERATE` is independently configurable from the model behind `CONVERSATION` in `DaemonSettings`). Operators who want narrative voice produced by their conversational model — because that's the voice they've tuned for their users — set the env var to `conversation` and no other change is needed. Validation: `FSF_NARRATIVE_TASK_KIND` must parse to a valid `TaskKind` value; daemon refuses to start with a bad value rather than silently defaulting.

3. **Caching strategy** for simultaneous twin births. **Deferred.** No in-process LRU keyed on `(dna_full, role, model_tag)`. Twin agents are rare; cost of an extra LLM call is small; the in-process cache would be invalidated by a daemon restart anyway. Reconsider if twin-spawning becomes a hot path.

4. **`max_tokens` cap** for the Voice generation. **Default 400** ("2–4 short paragraphs"). Operator-overridable via `FSF_NARRATIVE_MAX_TOKENS` (1–8192) for testing and tuning. The 400 baseline gets calibrated against real model output during implementation; this ADR doesn't lock it.

## Implementation contract — derived from the resolutions above

- **New env vars**, all with sensible defaults (no `.env` change required for a fresh clone):
  - `FSF_ENRICH_NARRATIVE_DEFAULT: bool = True` — global default for `BirthRequest.enrich_narrative` when the field is omitted. Tests that need deterministic behavior can either set this false in their settings fixture or pass `enrich_narrative=False` per-request.
  - `FSF_NARRATIVE_TASK_KIND: TaskKind = "generate"` — task_kind passed to `provider.complete()` for narrative rendering.
  - `FSF_NARRATIVE_MAX_TOKENS: int = 400` — `max_tokens` passed to `provider.complete()`.
  - `FSF_NARRATIVE_TEMPERATURE: float | None = None` — when set, passed through. Unset → provider default.

- **`BirthRequest.enrich_narrative: bool | None = None`**. None means "use FSF_ENRICH_NARRATIVE_DEFAULT". Explicit true/false overrides the setting per-request.

- **`soul/voice_renderer.py`** exports `render_voice(provider, profile, role, lineage, settings) -> VoiceText` (async). On any provider exception, returns a `VoiceText` with `provider="template"` and a templated fallback paragraph.

- **`SoulGenerator.generate()`** gains `voice: VoiceText | None = None` keyword arg. When supplied, the section is emitted; when None, no Voice section is emitted (preserves the existing-soul-file rebuild path that doesn't re-call the LLM).

- **Frontmatter emission** (in `_emit_frontmatter`): when `voice` is supplied and not None, three additional fields (`narrative_provider`, `narrative_model`, `narrative_generated_at`) are written between `agent_version` and the lineage block.

- **Audit event payload** (`event_data` for `agent_birthed` / `agent_spawned`): three additional optional fields mirroring the frontmatter. No schema bump — they're additive optionals.
