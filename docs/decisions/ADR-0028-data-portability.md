# ADR-0028 — Data portability spec (export / leave guarantee)

- **Status:** Placeholder — Deferred to v0.3+ (Phase A audit 2026-04-30 §A-1).

**Deferral rationale:** Data portability — export / leave guarantee. Deferred to v0.3+: v0.1 stores everything locally on the operator's disk in plain files (soul.md / constitution.yaml / audit_chain.jsonl). "Export" is `tar -czf` of the data dir today. v0.3 formalizes the portability format + adds a structured export endpoint.
- **Date:** 2026-04-27
- **Triggers when:** Before the v0.1 public release; before any feature stores user data outside the local artifact tree.
- **Related:** ADR-0006 (artifacts as canonical storage — this ADR formalizes the user-facing contract built on top), ADR-0024 (horizons), ADR-0027 (memory privacy — deletion semantics interact).

## Why this is a placeholder

The local-first architecture already gives users *de facto* data portability — every agent's soul.md, constitution.yaml, and audit chain entries live as plaintext on disk in the user's filesystem. ADR-0006 makes the artifacts authoritative; the SQLite registry is just an index that can be rebuilt.

What's missing is the **explicit user-facing contract**: "your data is yours, here's exactly what we guarantee, here's the export tool, here's how deletion works." Without that contract, the technical capability doesn't translate into a trust signal.

GDPR Article 20 (right to data portability) and similar regimes formalize this. Even without legal pressure, "you can leave whenever you want, here's how" is one of the strongest open-core differentiators.

This stub exists so when the v0.1 release begins, the portability story is part of the launch — not retrofitted later.

## Sketch of what this will cover

- **Export format**: tarball of `souls/` + `constitutions/` + `audit/` + `registry.sqlite`? Plus a manifest.json with the schema versions of each? Plus optional encryption?
- **Re-import**: a documented procedure to import the export into a fresh FSF install. The artifacts-authoritative property means this should work today; this ADR confirms it as a contract.
- **Selective export**: per-agent (single agent + ancestry) vs. full instance. Per-lineage exports for "give me my Companion line."
- **Deletion contract**: what "delete" means. Today, archive marks status — files stay on disk. Hard delete would need a path that preserves audit-chain integrity (probably tombstone entries rather than line removal).
- **Cloud-tier portability** (Horizon 3): when hosted realms exist, the same export contract has to extend across that boundary. Cloud users get the same .tar export of their data plus realm-state snapshots.
- **Verification**: an export should include a Merkle root of the audit chain so a recipient can verify the export hasn't been tampered with mid-transit. Cheap to add, easy to lose if not designed in.

## Cross-references

- ADR-0006 — registry/artifact split (this ADR rests on it).
- ADR-0027 — memory privacy (deletion in shared memory contexts).
- ADR-0024 — horizons.
