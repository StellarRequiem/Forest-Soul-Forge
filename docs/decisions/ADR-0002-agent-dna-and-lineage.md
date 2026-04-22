# ADR-0002 — Agent DNA and Lineage

- **Status:** Accepted
- **Date:** 2026-04-21
- **Supersedes:** —
- **Related:** ADR-0001 (hierarchical trait tree)

## Context

The trait tree produces infinitely many possible agent profiles. When we start
spawning agents from other agents — the factory pattern described in the vision
brief, plus specialized agents that spin up swarms — two things get hard fast:

1. **Identity.** Two agents that look alike in the UI may differ in one trait
   value and behave very differently. We need a fingerprint that is unique to
   an exact profile and reproducible: same inputs → same fingerprint.
2. **Lineage.** When an agent is the product of another agent's reasoning,
   we need to be able to prove which agent spawned it, and trace any agent
   back to its root human-created ancestor. This matters for blame
   assignment, replay, and detecting runaway spawn cascades.

Nothing in the current design addresses either. Every soul.md at the end of
Phase 1 was essentially anonymous — `NetworkWatcher v1` in the header with no
machine-checkable identity.

## Decision

Introduce two concepts: **Agent DNA** and **Lineage**.

### Agent DNA

A DNA is a sha256 hash over the canonical serialization of the identity-bearing
fields of a `TraitProfile`:

- `role` — string
- `trait_values` — mapping from trait name → int in `[0, 100]`, keys sorted
- `domain_weight_overrides` — mapping from domain → float, keys sorted

Serialization is `json.dumps(payload, sort_keys=True, separators=(",", ":"))`
in UTF-8. No timestamps, no agent name, no lineage — those vary between
generations of the same agent, and we don't want identity to rotate for
reasons unrelated to behavior.

Two display forms:

- **dna_full** — 64-char hex digest (stored in frontmatter).
- **dna** (short) — first 12 hex chars (shown in headers and references, like
  a git short SHA). 12 chars = 48 bits, sufficient collision resistance for a
  personal-scale agent population; the full form is always present for
  disambiguation.

Implementation lives in `src/forest_soul_forge/core/dna.py`, with a public
`verify(profile, claimed_dna) -> bool` that accepts either short or full form.

> **Amendment (2026-04-21, during ADR-0003 implementation):** originally placed
> at `src/forest_soul_forge/soul/dna.py`. Moved to `core/` so that the grading
> engine (also in core) can import DNA without inverting the layer direction.
> DNA is an identity primitive over a `TraitProfile` (a core concept), so
> `core/` is its correct home. The public API is unchanged; only the import
> path moved (`forest_soul_forge.soul.dna` → `forest_soul_forge.core.dna`).

### Lineage

Every generated soul.md carries a `Lineage` record:

```
Lineage {
    parent_dna: str | None      # short DNA of direct parent, or null for root
    ancestors: tuple[str, ...]  # root-first chain of short DNAs
    spawned_by: str | None      # direct parent's agent_name
}
```

- **Root agents** (created by a human through the factory UI/CLI) have
  `parent_dna=None`, empty `ancestors`, `spawned_by=None`.
- **Spawned agents** inherit their parent's `ancestors` chain, appending the
  parent's DNA at the end. The new child's own DNA is **not** in its own
  ancestor list — you read it from the header.
- Construction is `Lineage.from_parent(parent_dna, parent_lineage, parent_agent_name)`.

Lineage is metadata. It is **not** hashed into DNA — otherwise every
descendant would invalidate its own hash the moment it was minted, and two
structurally identical agents spawned by different parents would get different
DNAs, which defeats the point of using DNA as a behavior fingerprint.

### Frontmatter in soul.md

Every generated soul.md begins with a YAML frontmatter block containing:

```yaml
---
schema_version: 1
dna: <short>
dna_full: "<64 hex>"
role: <name>
agent_name: "<display name>"
agent_version: "v1"
generated_at: "YYYY-MM-DD HH:MM:SSZ"
parent_dna: <short | null>
spawned_by: "<name> | null"
lineage:
  - <ancestor short dna>
  - ...
lineage_depth: <n>
trait_values:
  <trait>: <int>
  ...
domain_weight_overrides:
  <domain>: <float>
  ...
---
```

This makes soul.md the single canonical storage format: it is both the LLM
prompt *and* the machine-readable record of the agent. Any consumer can
re-hash `trait_values` (plus role and domain_weight_overrides) and compare
against `dna`.

A human reader scans the header; a tool parses the frontmatter; nothing is
duplicated out-of-band.

## Consequences

### Positive

- Every agent gets a stable, reproducible, tamper-evident identifier.
- Lineage is auditable end-to-end: any spawned agent's claim-of-origin is a
  chain of hashes that can be verified by re-reading each ancestor's
  frontmatter.
- A spawn cascade that goes wrong can be traced to the generation that
  introduced the bad trait shift.
- soul.md becomes self-contained — no need for an external registry to know
  what an agent is.
- Golden-file testing becomes trivial: DNA change means behavior change.

### Negative

- YAML frontmatter adds ~30 lines to every soul.md. Tolerable — the file is
  not huge and the bulk is in the prose body.
- DNA only fingerprints identity, not runtime state. An agent that drifts from
  its defined profile during execution has the same DNA as its honest twin —
  that's what the audit chain is for, not DNA.
- 12-char short form has ~48 bits of collision resistance; at 10k agents the
  birthday probability is still vanishingly small, but if the system ever
  reaches millions of agents we'd want to widen the short form. Recorded as
  a future-work note, not a blocker.

### Neutral

- Parent information lives in the child's frontmatter only. The parent has
  no inherent knowledge of which children it spawned. If we want
  parent→children lookup later, we'll build an index; we're not baking it
  into the primary data structure.

## Alternatives considered

**UUID per agent.** Guarantees uniqueness but carries no information about the
agent's content — two identical profiles would get different UUIDs, which
breaks deduplication and reproducibility arguments. Rejected.

**Content-addressed store with pointer indirection.** Overkill at this scale
and hides what's already trivially inspectable. Rejected for Phase 1–3.

**Include lineage in DNA.** Makes every generation re-fingerprint itself,
destroying the "same profile = same DNA" property and inflating storage.
Rejected.

**Merkle tree of spawning events.** Powerful but premature. The linked-hash
chain in the `lineage` list already gives us tamper-detection: altering any
ancestor's frontmatter changes its DNA, which breaks every descendant's
recorded ancestor chain. Merkle upgrade is noted as a possible Phase 5 audit
enhancement.

**No lineage at all.** Considered and rejected: swarms and spawning agents are
already in the vision brief; retrofitting lineage later would require
regenerating every agent.

## Open questions

- **Lineage depth cap.** Do we want a configured max depth to prevent runaway
  self-spawn cascades? Currently unbounded. Revisit when the agent factory
  lands.
- **Cross-repo lineage.** If an agent spawned in Forest Soul Forge is later
  imported into the main Forest ecosystem, the ancestor chain should still
  verify. That's a property we get for free as long as the hashing is
  deterministic and the YAML schema is stable across repos — but worth
  confirming once the import path exists.
- **Parent attestation.** Should a parent agent sign its children's lineage
  entry (e.g., Ed25519)? Pure hash-chain is tamper-evident but not
  non-repudiable. Phase 5 candidate.
