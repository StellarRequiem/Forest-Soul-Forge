# ADR-0093 — Chronological canon: machine-generated state + drift gate

**Status:** Accepted (2026-06-03). Shipped in commit `f8337c9`:
`dev-tools/state_canon.py` (generator + `--check` gate), STATE.md restructured
into a fenced CANON block + a SUPERSEDED-banner CHRONICLE, `pyproject` version
corrected `0.2.0 → 0.5.0`, `tests/unit/test_state_canon.py`, and
`.github/workflows/canon.yml` (FSF's first CI).

## Context

STATE.md is a long-lived document that accretes a fresh status snapshot at every
phase boundary. Over 400+ bursts it came to hold three stacked strata
(2026-06-01 canon → 2026-05-19 B420 → 2026-05-13 B258) in the **same visual
register**, with only a quiet prose line separating "current" from "historical."

The failure this invites is concrete, not theoretical. An external reviewer
inspecting the public repo pulled `59,602 LoC / 2,800 tests / schema v20 /
"tamper-PROOF"` from the buried B258 stratum and reported it as the **current**
truth — contradicting the (correct) README headline of `101,623 LoC / 5,339
tests / v23`. The reviewer's own thesis — *"this repo tells too many versions of
its own truth"* — was proven by the reviewer tripping over exactly that. For a
project whose product promise is governance and auditability, a documentation
layer that misstates its own present is a credibility wound.

Two compounding causes:

1. **Hand-typed counts rot.** The numbers in STATE.md/README are typed by a
   human at refresh time and silently drift as code lands. Re-measuring at
   sign-off showed the *current* canon block was itself already stale on five
   fields (LoC `101,623`→`101,645`, ADR-unique, HEAD, commit count, audit length).
   `dev-tools/check-drift.sh` (Burst 82) checked some of these but greps `head -1`
   — which, against a now-stratified STATE.md, reads whichever stratum comes
   first, the very fragility that bit the reviewer.
2. **History is undifferentiated from the present.** Old snapshots are valuable
   audit lineage (operator protocol: *audit everything, append-only*) and must
   not be deleted — but they must be unmistakably marked as past.

This is the same claims-vs-ground-truth discipline as **ADR-0063 (Reality
Anchor)** and the operator's `firewall`/`grounded` tooling, turned reflexively on
FSF's own documentation. It is a process/discipline decision in the family of
**ADR-0082 (Kernel-Freeze Posture)**, not a feature.

## Decision

Adopt **CANON / CHRONICLE / GATE** for all numeric self-description.

- **CANON** — the present. A single generator, `dev-tools/state_canon.py`,
  measures disk and writes the current-truth table into STATE.md *between
  `<!-- CANON:BEGIN -->` / `<!-- CANON:END -->` fences* and mirrors it to
  `dev-tools/state_canon.json`. The block is machine-owned; hand-editing it is
  prohibited.
- **CHRONICLE** — the past. Superseded strata stay in the file for audit lineage
  but each sits behind a loud `⛔ SUPERSEDED — as of <date> — DO NOT quote as
  current` banner. The B258 banner additionally names its specific landmines
  ("tamper-PROOF", "v20", "57 ADR", "2,800 tests") and points to the live value.
- **GATE** — `state_canon.py --check` re-measures disk, diffs the committed
  canon, and exits non-zero on drift. It runs in CI (`canon.yml`) so the canon is
  self-healing: drift → red → re-emit.

**Decision 1 — no hand-maintained counts.** LoC, ADR counts, builtin-tool count,
structural test count, package version, and latest tag in the CANON block are
generated, never typed. Their source of truth is the generator's measurement of
disk, full stop.

**Decision 2 — four honesty tiers; only content is gated.** Every fact is
classified, and the classification governs whether it can fail the gate:

| Tier | Examples | Gated? | Why |
|---|---|---|---|
| `repo` | LoC, ADRs, tools, structural test count, version, latest tag | **hard** | content-derived, commit-agnostic, reproducible from any checkout |
| `provenance` | HEAD sha, commit count | no | advances every commit; a PR merge ref differs from branch HEAD — gating it cries wolf on every PR |
| `runtime` | registry agent counts, audit-chain length | no | volatile / host-local; the registry DB is gitignored (absent in CI), the chain grows continuously |
| `declared` | schema version | no | not static-measurable (registry `PRAGMA user_version` is 0; `/healthz` is the live check) — surfaced as declared, never as measured |
| `dynamic` | suite GREEN / pass-count | no | requires execution; the canon records the *structural* test count and defers pass-count to the CI artifact. It does **not** assert "N passing." |

**Decision 3 — the chronicle is append-only and never deleted.** Superseded
snapshots are fenced, not removed. History is audit evidence.

**Decision 4 — definitions are explicit and machine-fixed.** "Unique ADRs" =
distinct identifier stem after `ADR-` (dedups amendments like `ADR-0021-am`,
counts non-numeric placeholders `ADR-003X`/`003Y`) — this reconciles to the
project's count of 86 and is pinned by a test so a re-definition can't drift in
silently.

**Decision 5 — the gate is the release precondition.** No tag, and ideally no
merge to `main`, with the drift gate red. `dev-tools/check-drift.sh` is retained
as a human-readable broad view but is no longer authoritative for the gated
subset; `state_canon.py --check` is.

## Consequences

**Positive.** The reviewer incident cannot recur: the present is generated (can't
drift) and the past is loudly fenced (can't be misquoted). FSF gains its first
CI and a reproducible artifact behind its test-count claim. The mechanism is the
operator's own verification discipline applied to itself — the strongest possible
demonstration of the thing FSF claims to sell.

**Costs / limits.** (a) Contributors must run `--emit` after any content change
or CI fails — that friction *is* the enforcement. (b) The committed canon is
one commit stale on the `provenance` tier by construction (you can't know the
commit sha before committing); this is why provenance is non-gated. (c) The
README headline is **not yet** under the generator — it still carries a hand-typed
LoC that lags canon by ~22 lines; closing that (rendering the README headline
from the same canon) is the immediate follow-up.

**Follow-ups.** (1) Bring the README headline under `state_canon.py`. (2) Wire
`--check` into the **ADR-0079** daily diagnostic harness and a pre-commit hook so
drift is caught locally, not only in CI.

## Alternatives considered

- **Tighter human discipline ("just bump the doc").** Rejected — this is exactly
  what produced 400 bursts of drift and the reviewer incident. Burst 82 already
  learned this lesson once.
- **Delete historical snapshots to remove ambiguity.** Rejected — it destroys
  audit lineage, violating *audit-everything / append-only*. Fence, don't delete.
- **Full document generation (generate all of STATE.md/README).** Deferred —
  heavier, and most of those docs are prose that benefits from human authorship.
  Only the numeric counts need machine ownership; that is what this ADR scopes.
