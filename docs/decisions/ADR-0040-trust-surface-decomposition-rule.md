# ADR-0040 — Trust-Surface Decomposition Rule

- **Status:** Accepted (filed 2026-05-02 in conversation; promoted to Accepted on landing because it codifies a rule the orchestrator already endorsed).
- **Date:** 2026-05-02
- **Supersedes:** —
- **Related:** ADR-0006 (registry as derived index), ADR-0019 (tool execution runtime), ADR-0021 (role genres + initiative ladder), ADR-0022 (memory subsystem), ADR-0027 (memory privacy contract), ADR-0036 (Verifier Loop), ADR-0039 (Distillation Forge §4 — "no god objects, grow new branches grounded by a solid feature"), ADR-0034 (SW-track triune — the trio that drives R-track refactors).

## Context

The standard rule in software engineering is "split god objects." The reasoning is human-centric:

- A human can't hold 1500 LoC in their head
- New contributors can't onboard onto a god object safely
- Code review across many lines is harder
- Merge conflicts between human collaborators
- Refactoring without breaking unrelated areas

For an **AI-operated codebase**, several of these soften:

- Re-reading 1177 LoC each session is cheap for an AI
- Onboarding doesn't apply the same way
- Multiple AI operators coordinate through shared state (audit chain, task list) rather than file-locking
- Code review is structural, not screen-fitting

This ADR captures the orchestrator's reframe (2026-05-02 conversation):

> "Maybe the god objects and things like that aren't a bug or a problem
> we should see them as a feature potentially — things that were
> problems with human operators could be mitigated with AI. If we can
> put redundancy and safeties in place that are as robust as we can
> make them, maybe that would be enough."

The reframe is partially correct. Some traditional decomposition was driven by team-coordination concerns that don't apply when AI is the operator. But four costs DON'T go away with AI operators, and three of them are FSF-specific:

1. **Per-burst test cost** — A god object whose change might affect any of N concerns means the safe thing is to run the full test surface on every change. That's a real per-burst cost that scales with file size, not with operator type.
2. **§0 Hippocratic gate blast-radius reasoning** — The §0 gate requires "prove no harm" before a removal. The blast radius of a change in a god object is larger by definition. AI operators can scan fast, but the *reasoning* about what could break is still O(file size). Errors get more likely.
3. **FSF's allowed_paths governance is file-grained (FSF-specific)** — Constitutions specify `allowed_paths`. An agent given edit access to a god-object file implicitly gets edit access to *all* concerns the file mixes. An agent that should only flag contradictions in `memory_contradictions` could, if `memory.py` is the god object, also stamp verifications, grant consents, or write core memory rows. That collapses the genre/initiative-level/kit governance discipline into "do you have the file or not."
4. **Constitution hash discipline cascades (FSF-specific)** — Agents are born against content-addressed hashes. If `memory.py` is the dependency, every edit (even unrelated to that agent's concerns) invalidates the agent's binding. Agents end up needing rebirth for changes in concerns they don't even use.

The reframe applies cleanly to **cohesive** god objects (single trust surface, just large) but breaks down for **non-cohesive** god objects (files that bundle multiple distinct trust surfaces).

## Decision

### §1 — The trust-surface-count rule

A file warrants decomposition when it bundles **two or more distinct trust surfaces**, regardless of line count. A trust surface is one of:

- A class of operation that requires its own `allowed_paths` policy in a constitution
- A class of operation that emits its own audit-event family
- A class of operation gated by a different `required_initiative_level`
- A class of operation with distinct cross-agent disclosure semantics
- A class of operation at a different privacy scope ceiling

If a file mixes ≥ 2 of these, the file's contents inherit the union of all the trust surfaces' policies — meaning *no* agent can be constrained tighter than the loosest policy in the file. That's a governance loss.

### §2 — Cohesive god objects are fine

A file at any size that has **one trust surface** can grow without decomposition, IF the surface is also covered by the safeties listed in §3. Examples of cohesive surfaces in the current codebase:

- `tools/dispatcher.py` — single trust surface (tool dispatch + governance pipeline)
- `core/audit_chain.py` — single trust surface (chain integrity)
- `core/constitution.py` — single trust surface (constitution build)
- `core/trait_engine.py` — single trust surface (trait/role resolution)
- `core/genre_engine.py` — single trust surface (genre policy)

These can grow to 2000+ LoC without violating this ADR, provided §3 holds.

### §3 — Required safeties for cohesive god objects

A file claiming "single trust surface, no decomposition needed" must have:

1. **Per-public-method invariant docstrings** — every public method documents the invariants the body preserves.
2. **Property-based invariant tests** — for each documented invariant, an automated test that exercises it.
3. **Pre-commit static analysis** — `ruff_lint.v1`, `mypy_typecheck.v1`, `bandit_security_scan.v1`, `semgrep_scan.v1` on every diff. (Already shipped in Phase G.1.A — these are the substrate for the safety claim.)
4. **Audit-chain coverage** — every state-changing method emits a typed audit event from `KNOWN_EVENT_TYPES`.
5. **Behavioral signature tests** — every public method has at least one test that pins its observable behavior, so internal refactors can't accidentally change the surface.

Without these, the file is at risk regardless of size; with these, the file is safe at almost any size.

### §4 — Non-cohesive god objects MUST decompose

A file bundling ≥ 2 trust surfaces is on the decomposition list, ranked by trust-surface count. Each decomposed module:

- Owns one trust surface
- Has its own test file
- Has its own ADR or audit-doc reference for the surface's semantics
- Is reachable in agent constitutions as a more-precise `allowed_paths` entry

### §5 — Required decomposition list (current)

| File | Lines | Trust surfaces | Decomposition target |
|---|---:|---|---|
| `core/memory.py` | 1177 | **5** (core CRUD, consents, verification, challenge, contradictions) | `core/memory/` package: `_helpers.py`, `_core_mixin.py`, `_consents_mixin.py`, `_verification_mixin.py`, `_challenge_mixin.py`, `_contradictions_mixin.py`, `__init__.py` (Memory class) |
| `daemon/routers/writes.py` | 1183 | **~9** (birth, spawn, archive, character_sheet writes, voice_renderer writes, consent admin, etc.) | `daemon/routers/writes/` package, one router file per resource |

Other current files inspected and confirmed cohesive (no decomposition needed):

| File | Lines | Trust surface |
|---|---:|---|
| `tools/dispatcher.py` | (large) | tool dispatch + governance pipeline |
| `core/audit_chain.py` | (large) | chain integrity |
| `core/constitution.py` | (medium) | constitution build |

### §6 — When to apply: at trust-surface boundary, not size threshold

Don't run a periodic "files over N lines" audit. Instead, run a "files mixing trust surfaces" audit at every phase boundary (Phase A close, Phase B close, etc.). A file that adds a new trust surface in any commit goes on the decomposition list at that moment, regardless of size.

### §7 — Decomposition mechanics: mixin-class pattern

Where a single class has multiple trust surfaces (the `core.memory.Memory` case), decompose using **mixin classes**:

```python
# core/memory/_core_mixin.py
class _CoreMixin:
    def append(self, ...): ...
    def recall(self, ...): ...
    def get(self, ...): ...

# core/memory/_consents_mixin.py
class _ConsentsMixin:
    def grant_consent(self, ...): ...
    def revoke_consent(self, ...): ...

# core/memory/__init__.py
from ._core_mixin import _CoreMixin
from ._consents_mixin import _ConsentsMixin
# ...

class Memory(_CoreMixin, _ConsentsMixin, ...):
    """Public API surface. Methods inherit from per-surface mixins."""
    def __init__(self, conn): ...
```

Why mixins over composition (`memory.contradictions.flag(...)`):

- Preserves the public API exactly. Existing callers don't break.
- Each mixin is testable in isolation (the test file binds a stub conn + the mixin only).
- `allowed_paths` constraints can target the mixin file specifically (`core/memory/_contradictions_mixin.py`), not the union.

## Trade-offs and rejected alternatives

**"Just keep the file at 1177 LoC, add safeties."** Rejected for non-cohesive god objects. The §3 safeties are necessary but insufficient — they don't fix the agent-governance scope blast radius (§Context point 3). Method-level `allowed_paths` is the alternative, which is harder than just splitting the file.

**Mandatory size threshold (e.g. "split anything over 500 LoC").** Rejected. Size-threshold rules cause premature decomposition of cohesive surfaces, which adds indirection without governance gain. The trust-surface-count rule is principled and lines up with FSF's value proposition (fine-grained governance).

**Composition over mixins.** Rejected. Composition (`self.contradictions = ContradictionsModule(conn)`) breaks the public API (callers go from `memory.flag_contradiction(...)` to `memory.contradictions.flag(...)`) and forces a coordinated migration of every caller. Mixins preserve the API.

**Defer all decomposition to after v1.0.** Rejected. The longer non-cohesive god objects sit, the more concerns accrete and the more expensive the eventual split becomes. `core/memory.py` already grew +311 LoC in the v0.3 ADR-0036 arc. Each added concern compounds the decomposition cost.

**Embrace god objects everywhere with method-level `allowed_paths`.** Rejected. Method-level `allowed_paths` is its own major architectural project (changing the constitution schema, the dispatcher gate, the audit-chain entries, the agent-birth flow). Roughly the same work as decomposing the affected files, with weaker resulting safety properties (a typo in a method name vs. a typo in a file path — both are bugs but the file-path version is harder to land silently).

## Consequences

**Positive.**
- Captures the trust-surface-count principle so future sessions don't relitigate the size-vs-cohesion debate.
- Lists the current decomposition queue (`core/memory.py`, `daemon/routers/writes.py`) as concrete tranches.
- Defines the safeties §3 that cohesive god objects need — five concrete things, all measurable.
- Lets future ADRs (0035, 0037, etc.) reference this rule when they create new files.

**Negative.**
- Two R-track refactors (memory + writes) are now scope-committed. Each is multi-burst work.
- The mixin pattern is non-default Python; new contributors (and AI operators) must learn the convention.
- Future static-analysis tooling needs to understand the mixin pattern to attribute methods correctly (in particular, `allowed_paths` enforcement should resolve mixin classes to their source files).

**Neutral.**
- Adds a vocabulary item ("trust surface") that future ADRs must use consistently.

## Cross-references

- ADR-0021-amendment §3 — initiative ladder (one of the trust surfaces a file might mix)
- ADR-0027 §5 — privacy ceilings (one of the trust surfaces)
- ADR-0027-am §7.3 — memory_contradictions surface (a recent example of a new trust surface arriving in `core/memory.py`)
- ADR-0036 §1 — Verifier as new branch (an example of §1 done correctly: new feature got its own package, didn't bloat memory.py — though it added contradictions methods to memory.py, which contributed to the decomposition list)
- ADR-0039 §4 — "no god objects, grow new branches grounded by a solid feature" (this ADR is the operationalization of that rule for INTRA-FILE concerns)

## Open questions

1. **Should this rule apply retroactively to `daemon/routers/writes.py`?** It's been on the decomposition list since the Phase A audit (2026-04-30). This ADR confirms it's a real concern, not a vague code-smell. Likely Burst 73+ work after the memory refactor lands.

2. **What's the test discipline for mixin classes?** The mixins reference `self.conn` etc. without declaring the interface. Should we use `Protocol` types to make the mixin's expectations explicit? Decision deferred until the first split lands; revisit then.

3. **Does this affect existing READMEs / contributor docs?** STATE.md should reference this ADR in the "Conventions a contributor needs to know" section. Will land when STATE.md gets its next update (probably the v0.3.0 paperwork commit if/when v0.3 ships).

4. **Performance: do mixins have measurable import / dispatch overhead?** Python attribute lookup walks the MRO; for a 5-mixin class the cost per method call is ~5 dict lookups vs ~1 for a flat class. Almost certainly negligible at FSF's call rate, but worth measuring on the dispatch hot path if the dispatcher itself is ever decomposed this way.

## Implementation tranches

- **T1** — File this ADR. (Burst 71, this commit.)
- **T2** — Apply rule to `core/memory.py`. Convert to a `core/memory/` package with `_helpers.py` extraction first (lowest-risk piece), then per-surface mixins (`_core_mixin.py`, `_consents_mixin.py`, `_verification_mixin.py`, `_challenge_mixin.py`, `_contradictions_mixin.py`). Public API exactly preserved via `__init__.py` re-exports. Test suite green at every step. Estimated 2-3 bursts (≈ Bursts 72-74).
- **T3** — Apply rule to `daemon/routers/writes.py`. Convert to a `daemon/routers/writes/` package, one file per resource (birth, spawn, archive, etc.). Wire up `app.include_router()` for each. Estimated 2-3 bursts.
- **T4** — Update STATE.md "Conventions" section to reference this ADR. Update CLAUDE.md if any operator-facing conventions change. Single small burst.

After T1-T4 land, the trust-surface-count rule is the operational discipline; future ADRs that introduce new trust surfaces (e.g. ADR-0035 Persona Forge, ADR-0037 Observability) just file their own package from the start rather than adding to existing files.

## Attribution

The principle of evaluating god objects by trust-surface count rather than line count emerged from the orchestrator's 2026-05-02 conversation about cost management for the project. The orchestrator's reframe ("maybe god objects aren't a bug — robust safeties could substitute") was the catalyst; the synthesis ("yes for cohesive surfaces; no for non-cohesive ones, because file-grained governance breaks") is FSF-specific work shaped by ADR-0021 (genres + initiative ladder) and ADR-0027 (privacy contract).
