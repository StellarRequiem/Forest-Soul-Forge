# The Standard

*A measurable, independently-verifiable bar for work. Maintained by **StellarRequiem**.*

## Why this exists

Trust is earned by verification, not narrative. This document defines — in
measurable, independently-checkable terms — what *done* means for any unit of
work under this account, so a skeptic can confirm the bar was met **without
taking anyone's word for it**. The work is the credential; this is the rubric it
is held to.

## The bar

A unit of work meets the Standard only when each of the following is true **and
measurable**:

| # | Criterion | The measure | How a third party verifies it (no trust required) |
|---|---|---|---|
| 1 | **Tested** | automated tests cover the change; the suite is green | the CI run · `pytest` locally |
| 2 | **Verified** | claims proven against the *running* system, with live proof | a VERIFIED block — Tested / Results-with-numbers / Live-proof / Gaps |
| 3 | **Audited** | every state-affecting action lands on an append-only, hash-linked log | `fsf verify` → *integrity intact* · the audit chain |
| 4 | **Calibrated** | any claimed *edge* is logged as a prediction and scored over time | the public calibration-log · Brier score / hit-rate vs. base rate |
| 5 | **Honest gaps** | known limits stated plainly, not omitted | a non-empty, truthful Gaps section |

**Meta-standard — independently verifiable.** Every claim above must resolve to
evidence a third party can re-run or read (CI · `git` history · `fsf verify` ·
the calibration-log). *If a claim cannot be independently checked, it does not
count.*

## The scorecard

Each unit of work scores **0–5**, one point per criterion met. Criterion 4 is
*N/A* when the work makes no predictive claim, in which case the score is out of
the applicable criteria. A passing unit is full marks. The score is recorded;
the **trend over time is the track record.**

## Disqualifiers (the red lines)

- **Narrative in place of proof.** A theoretical, fictional, or un-runnable
  "source" carries zero epistemic weight.
- **A believed result that hasn't been falsified.** A win-rate over ~65% is
  auto-suspect — a bug to disprove before it is a feature.
- **A state-affecting claim with no audit entry.** If it isn't on the chain, it
  didn't happen.
- **Single-writer violated.** One writer at a time touches a live store; the
  daemon holds the lock (enforced — `core/single_writer.py`).

## How it is hard-coded

This is not an aspiration document. It is wired into the systems that produce
the work:

- **Verification is a command** — `fsf verify` reads the on-disk artifacts
  directly (no daemon, trusts nothing the daemon says) and exits non-zero if any
  integrity check fails. Scriptable, so it gates.
- **Integrity is enforced** — append-only hash-chained audit, cross-process
  single-writer lock, CI canon-drift gate.
- **The bar binds the operator's agent** — the same standard governs the AI
  extension that does the work (see the operator protocol), so it applies to
  *every* unit, not just the ones someone remembers to check.
- **Bridges + controllers** cover what can't be hard-coded directly — adapters
  and control surfaces that bring external systems under the same measurable,
  auditable bar.

## Attribution

Authored and maintained under the pseudonymous builder identity
**StellarRequiem**. Real-world identity is deliberately firewalled; the standing
is built on verifiable work, not a name.
