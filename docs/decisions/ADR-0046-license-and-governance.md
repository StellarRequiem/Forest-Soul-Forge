# ADR-0046 — License Posture + Governance

**Status:** Accepted 2026-05-05; **Amended 2026-05-12 (B245) — license
switched from Apache 2.0 to Elastic License 2.0 (ELv2).** Commits
through `f799757` (B244 — ADR-0049 tamper-proof chain) remain
irrevocably under Apache 2.0 per Apache's §4. From B245 onward the
Software is licensed under ELv2. See **Amendment 1** at the bottom
of this ADR for the full reasoning + cascade.

Phase 5 of the ADR-0044 kernel-positioning roadmap. Originally
confirmed Apache 2.0 (already in `LICENSE`) as the deliberate
choice for kernel-shape positioning; the 2026-05-12 amendment
reframes that decision given the platform-business posture
Alex committed to during the May 2026 license review.

## Context

ADR-0044 declared Forest as the kernel and identified the first
external integrator as the load-bearing v0.6+ milestone. Two
non-code surfaces gate that recruitment:

1. **License.** The license a kernel ships under shapes who can
   integrate. GPL forces ecosystem contribution back; Apache
   maximizes commercial adoption; BSL signals "we're going to
   monetize this directly." Forest's `LICENSE` file is already
   Apache 2.0, but no ADR has *justified* the choice — making
   it a soft default rather than a deliberate posture. Without
   the justification, a future maintainer (or Alex revisiting
   in 18 months) might second-guess it under hostile-fork
   pressure.

2. **Governance.** "Forest commits to backward compatibility on
   the seven kernel ABI surfaces at v1.0" (ADR-0044 Decision 3)
   is empty without a process for deciding what goes in v2, who
   maintains the spec, how external integrators get heard, and
   how disputes resolve. Today's reality: Alex is the sole
   steward. That's fine for v0.6, but the governance story
   matters to integrators evaluating whether to bet on Forest.

This ADR locks both decisions.

## Decision 1 — License: Apache 2.0

Forest's kernel and reference distribution (SoulUX) ship under
Apache License 2.0. The `LICENSE` file is the canonical text;
this ADR documents the *why*.

### Why Apache 2.0

**Maximizes external integrator paths.** ADR-0044 Decision 4
names recruiting an external integrator as the load-bearing
milestone. The likely candidates (agnt, AIOS, future commercial
distributions) need a permissive license to integrate without
legal friction. Apache 2.0 is the closest thing to a universally-
accepted permissive kernel license in 2026.

**Explicit patent grant.** Unlike MIT or BSD-2/3, Apache 2.0
includes a patent retaliation clause (§3): contributors grant
patent rights to users, and lose that grant if they sue another
user over patent claims related to the work. The kernel handles
sensitive things (trust dials, audit chains, governance
pipelines) where a future patent troll could surface; Apache's
patent posture is a meaningful defense.

**Maps to kernel-shape precedent.** PostgreSQL is its own
permissive license (close to MIT). Apache projects (Cassandra,
Kafka, Spark) are Apache-licensed. Linux is GPLv2 — but Linux
solved its ecosystem problem 30 years before SaaS pricing made
"share-and-share-alike" weaker as a coordination mechanism.
Modern kernel-shape projects (OpenTelemetry, Envoy, etcd) have
mostly chosen permissive licenses to maximize adoption.

**Compatible with the SoulUX Tauri distribution.** Tauri 2.x is
Apache-2.0 / MIT dual-licensed. The frontend dependencies (no
React + npm chain — vanilla JS) don't introduce GPL friction.
The PyInstaller-built daemon binary stays Apache.

### Why NOT the alternatives

**GPLv3 / AGPLv3.** Best for ecosystem return-flow: derivative
works must share back. But:
- An agnt-style commercial integrator can't ship a closed-
  source distribution that bundles a GPL-licensed Forest.
  Forces them to either GPL their whole distribution or NOT
  integrate.
- AGPL specifically targets the "SaaS loophole" but Forest is
  local-first by design — there's no SaaS hosting to defend
  against today.
- The downside is concrete (closes integration paths); the
  upside is hypothetical (ecosystem return-flow that may not
  materialize for a solo project).
- Net: GPL/AGPL is the right answer for projects that have
  already proven adoption and need to defend ecosystem health
  (Linux, MongoDB historically). Premature for Forest.

**BSL (Business Source License) / Apache + Commons Clause.**
Source-available with commercial restrictions. Pattern used by
Cockroach, Elastic, Confluent post-AWS-fork. But:
- Forest doesn't have an AWS-hostile-fork problem to solve.
- The "non-OSI-approved" status closes off communities (Linux
  Foundation, CNCF) that filter on OSI conformance.
- Signals "we plan to monetize this product directly" — the
  opposite of kernel-shape positioning.
- Net: wrong shape. Solves a problem Forest doesn't have at the
  cost of the kernel posture this ADR locks.

**MIT / BSD.** Permissive without the patent grant. Simpler
license text, but in a kernel that handles trust + audit
governance, the missing patent retaliation clause is a real
gap. Not worth the simplification.

**Dual-license (Apache + commercial).** What MongoDB / Sentry
did pre-AGPL. Adds licensing administration overhead (CLA
required, license tier decisions per integrator). Premature
for v0.6.

### What Apache 2.0 commits Forest to

- Anyone can fork, modify, redistribute, sell — including under
  different license terms — as long as they preserve the
  copyright + license notices.
- Forest contributors retain copyright on their own
  contributions (no copyright assignment to a corporate entity).
- Patent retaliation clause kicks in if a user sues over
  patents.
- No "share-back" obligation. A commercial distribution can
  improve Forest internally without contributing back.

### What Apache 2.0 does NOT solve

- **Hostile commercial forks.** AWS-style "host the open
  project as a managed service and capture all the revenue" is
  legal under Apache. We accept this risk; it's vanishingly
  unlikely for a v0.6 project.
- **Ecosystem fragmentation.** Two competing distributions both
  built on Forest could diverge their kernel forks over time.
  The mitigation is the kernel ABI commitment (ADR-0044
  Decision 3) — distributions that diverge from the spec stop
  being "Forest distributions."
- **Trademark protection.** Apache 2.0 doesn't grant trademark
  rights. "Forest" and "SoulUX" as product names are *not*
  Apache-licensed. Trademark posture is a separate decision,
  deferred until trademark filing matters (likely v1.0+).

### License compatibility matrix

For integrators evaluating Forest:

| Their license | Can they integrate Forest? |
|---|---|
| MIT / BSD / Apache | Yes, no friction |
| Mozilla Public License 2.0 | Yes |
| LGPL | Yes (as a library) |
| GPLv2 | Yes if they're GPLv2-or-later; not if strict GPLv2 |
| GPLv3 | Yes |
| AGPLv3 | Yes |
| Proprietary / closed-source | Yes — Apache permits |
| BSL / Commons Clause / SSPL | Yes — Apache permits redistributing under non-OSI terms |

The matrix is essentially "anyone can integrate." That's the
point.

## Decision 2 — Governance

### Maintainer model: single steward at v0.6

Forest is currently maintained by Alex (StellarRequiem). The
governance posture for v0.6+ is:

- **Single steward** — Alex is the BDFL-style maintainer.
  Final say on what goes in the kernel, what surfaces are
  ABI-committed, what the next release shapes look like.
- **Solo signing key** — Alex's GPG key signs releases when
  release-signing lands (post-Tauri T5).
- **Transition trigger to multi-maintainer:** the FIRST of:
  (a) An external integrator with sustained participation
  (5+ merged PRs spanning at least 3 months).
  (b) A second internal distribution shipping (e.g., a
  community-built TUI distribution alongside SoulUX).
  (c) Alex's request to onboard a co-maintainer for any reason
  (vacation coverage, succession planning, scaling).
- **No corporate copyright assignment.** Contributors retain
  copyright on their work. The kernel grows organically; no
  CLA-funneling-to-an-LLC pattern.

### RFC / ADR process

ADRs are Forest's public RFC mechanism. Anyone (contributor,
external integrator, lurker) can propose an ADR by opening a
PR against `docs/decisions/`. The format is whatever the
existing 45 ADRs use — title, status, context, decision,
consequences, references.

- **Proposer** writes the ADR.
- **Steward (Alex)** reviews + decides accept/reject/revise.
- **Public visibility** — every ADR lives in the public repo
  even if rejected (rejected ADRs get `Status: Rejected` and
  a brief why).
- **Amendments** — the existing
  ADR-0021-amendment / ADR-0027-amendment pattern continues
  for revising accepted decisions without rewriting history.

What ADRs cover (kernel-relevant decisions):
- Adding/removing an ABI surface from the v1.0 freeze list
- Schema migrations (additions are usual; restrictions need
  ADR justification)
- New audit event types
- New plugin manifest fields
- New CLI subcommands
- Governance changes (this ADR is the precedent)

What ADRs don't need to cover:
- Internal refactors that preserve the seven ABI surfaces
- Userspace changes (apps/desktop/, frontend/, dist/)
- Test additions
- Documentation updates (other than ADRs themselves)

### Conflict resolution

Disputes about "is this kernel or userspace?" or "should this
break ABI?" follow this escalation:

1. **Steward decides** — fastest path. Alex weighs the
   tradeoffs and writes an ADR.
2. **Public ADR comment thread** — if a proposer disagrees,
   they can argue in the ADR's PR comments. Steward may
   revise.
3. **Eventual second-maintainer review** — once the
   transition trigger fires, two-maintainer concurrence is the
   bar for kernel-ABI changes.

Today (v0.6, single-steward), step 1 is final. The discipline
is honesty + rigor in step 1's reasoning, not a vote.

### Code of Conduct

Forest adopts the Contributor Covenant 2.1 (or current). Filed
as `CODE_OF_CONDUCT.md` in a follow-up burst. Steward enforces;
escalation contacts will be the steward + a backup as the
maintainer pool grows.

### CLA / DCO

For v0.6, **no CLA**. Contributors retain copyright; their
contributions are licensed under Apache 2.0 by submission. This
matches the Linux kernel's DCO (Developer Certificate of
Origin) posture — "Signed-off-by" lines in commits are the
attestation.

The DCO mechanism may formalize in v0.7+ if commit-signing
discipline tightens. For now, contributions through GitHub PRs
are deemed Apache-2.0 by the LICENSE file's terms.

### Forking + distribution governance

Anyone can fork Forest under Apache 2.0. The governance
distinction Forest reserves:
- **"Forest" as a project name** — refers to the kernel as
  maintained by the steward(s). Forks can use the code, can't
  call themselves "Forest" without confusion.
- **"SoulUX" as a distribution name** — refers to the
  reference distribution maintained by the steward(s). Forks
  can build distributions on Forest, can't call themselves
  "SoulUX."
- **Kernel ABI conformance** — a distribution that diverges
  from the v1.0 kernel ABI stops being a "Forest
  distribution." This is enforced socially (project recognition,
  documentation cross-references), not legally (Apache permits
  divergence; the kernel project can't sue for it).

These are conventions, not enforcement. They become more
load-bearing once trademark posture is filed (deferred).

## Decision 3 — Public RFC location for the kernel API spec

ADR-0044 Phase 2 (`docs/spec/v1/`) will publish the formal
kernel API specification. This ADR locks where:
- **In-repo** at `docs/spec/v1/`. Versioned alongside code.
- **Authoritative** — matches the running implementation;
  drift is a bug to fix in either the spec or the
  implementation.
- **Public on the GitHub repo** — same visibility as ADRs.
  External integrators read it the way they read the LICENSE
  file: as the contract.

A separate `forest-spec` repository is *not* needed at v0.6.
Could revisit at v1.0 if the spec is extensive enough to
warrant separate version-tagging cadence from the
implementation.

## Consequences

**Positive:**

- Removes ambiguity about Forest's license stance.
  External integrators evaluating Forest see a deliberate,
  justified Apache 2.0 commitment.
- Documents the governance story so the kernel claim has a
  process to point at, not just code.
- Lists the transition triggers for moving past single-steward
  governance — gives Alex a forward-compatible model that
  doesn't force premature institutionalization.
- Defers expensive governance artifacts (CLA, trademark,
  separate spec repo) until the project has the scale to
  justify them.

**Negative:**

- Apache 2.0 accepts the hostile-commercial-fork risk. If
  Forest ever achieves significant adoption, an AWS-style
  competitor could appear. The mitigation (BSL relicense) is
  available but currently unused.
- Single-steward governance is a single point of failure. If
  Alex stops maintaining, the project stalls. Mitigation:
  the transition triggers above; the public ADR + audit chain
  + comprehensive doc set means a successor can pick up the
  trail.
- "Forest" and "SoulUX" trademark protections are deferred —
  can't currently sue a confusingly-named fork. Acceptable for
  v0.6.

**Neutral:**

- License doesn't change — the `LICENSE` file is already
  Apache 2.0. This ADR is justification, not a relicense.
- Governance is the *same* posture Forest has been operating
  under since inception. This ADR makes it explicit and
  forward-compatible rather than implicit and ad-hoc.

## What this ADR does NOT do

- **Does not create a CONTRIBUTING.md** — that's a follow-up
  burst.
- **Does not file CODE_OF_CONDUCT.md** — same.
- **Does not file trademark applications** — deferred.
- **Does not pick maintainer succession candidates** — only
  defines the *trigger* for promoting one.
- **Does not commit to a release-signing identity** — that's
  Tauri T5 (Apple Developer decision).

## References

- ADR-0044 — Kernel Positioning + SoulUX Flagship Branding
  (the parent ADR; this is its Phase 5 deliverable)
- `LICENSE` — the canonical license text (now ELv2 per
  Amendment 1; was Apache 2.0 through B244)
- `LICENSE.history` — operator-facing license-cutover summary
- ADR-0001 — Audit chain (the immutable governance evidence)
- Apache License 2.0 specification:
  https://www.apache.org/licenses/LICENSE-2.0
- Elastic License 2.0 specification:
  https://www.elastic.co/licensing/elastic-license
- Linux kernel governance model (BDFL → maintainer hierarchy
  precedent):
  https://www.kernel.org/doc/html/latest/process/index.html
- Contributor Covenant 2.1:
  https://www.contributor-covenant.org/version/2/1/code_of_conduct/

---

## Amendment 1 — License switch from Apache 2.0 to ELv2 (2026-05-12, B245)

### Why amend

The 2026-05-05 ADR-0046 rationale assumed external integrators
were the load-bearing v0.6+ milestone (per ADR-0044 Decision 4)
and that maximizing integrator paths via Apache 2.0 was the right
posture. The 2026-05-12 license review with Alex revisited this in
light of three things that changed during the v0.5/v0.6 work:

1. **The business model crystallized as a platform-host product.**
   Forest Soul Forge runs as a hosted service Alex operates; users
   download their agents (hardware-bound at birth per ADR-003X K6,
   cryptographically signed per ADR-0049 — both shipped) to run
   on their own machines. The hosted service is the commercial
   offering. This is structurally different from a kernel that's
   meant to be embedded in third-party distributions — it's an
   end-user product with infrastructure underneath.

2. **The ADR-0055 marketplace makes commercial defensibility load-
   bearing.** A marketplace that hosts community + commercial
   content needs the host (Forge SaaS) to be a viable business;
   Apache 2.0 lets a competitor stand up an identical Forge SaaS
   the day after Forest gets traction. ELv2's "no competing managed
   service" restriction closes that specific attack vector.

3. **Zero forks + zero external production users as of B244.** The
   relicense window is open. Switching after the first external
   integrator adoption creates obligation arguments that aren't
   present today. Switching now is the lowest-friction moment
   the project will ever have.

### What changes

- `LICENSE` replaced with the canonical ELv2 text per
  https://www.elastic.co/licensing/elastic-license
- Commits through B244 (`f799757`) remain irrevocably under Apache
  2.0 (Apache's §4: a release under Apache 2.0 cannot be revoked
  for that release). Anyone who pulled those versions retains
  their Apache 2.0 grants for THOSE versions.
- Commits from B245 forward are ELv2-only.
- `LICENSE.history` documents the cutover for operators reading
  the repo cold.
- `CONTRIBUTING.md` adds a contributor-licensing grant: future
  contributions are licensed under ELv2 + contributors grant the
  Licensor a perpetual right to relicense their contributions
  for any future Licensor-chosen license. This keeps Alex's
  flexibility to ever go back to Apache, MPL, or any other
  license without needing to track every individual contributor
  down for permission.

### What doesn't change

- Decision 1's *spirit* — Forest's source is still public and
  inspectable. ELv2 is source-available, not closed-source. The
  difference is permission to commercially redistribute as a
  managed service.
- Decision 2 (Governance — BDFL-style with maintainer hierarchy).
- The ADR-0044 kernel-ABI commitments. Integrators can still
  build distributions on top of Forest; they just can't offer a
  hosted-Forge-SaaS competing with the Licensor's offering.
- Backwards compatibility for existing Apache 2.0 forks of the
  pre-B245 codebase. Their rights are preserved by Apache's §4
  irrevocability.

### Why ELv2 and not BSL / FSL / SSPL / PolyForm

Considered alternatives during the review:

| Alternative | Why not |
|---|---|
| BSL 1.1 (HashiCorp pre-2024, Sentry pre-2024) | Time-delayed conversion to Apache. Adds "we'll give it back eventually" promise that the project's platform-business posture doesn't actually want. The 4-year window doesn't help when the business model assumes long-running platform service. |
| FSL 1.1 (Sentry current) | Same shape as BSL with 2-year delay. Same reason against. |
| SSPL v1 (MongoDB) | The "open your whole stack" copyleft is harsher than ELv2 and Fortune-500-hostile. Forest doesn't need that aggressive a posture against managed-service attacks; ELv2's three restrictions are sufficient. |
| PolyForm Noncommercial 1.0 | Blocks ALL commercial use without paid license. Would gate solo-developer commercial experimentation — the lowest-friction adoption path for Forest. Net negative for adoption. |
| Custom Forest Source License | Forest is small enough this would be feasible, but custom licenses force every prospective integrator's lawyers to do net-new review. ELv2 is well-known and battle-tested with enterprise legal teams (Elastic, Datawire, RedPanda, MongoDB-pre-SSPL). |

ELv2 hit the sweet spot of: clear-message, well-understood by
enterprise legal, no time-delay (matches platform-business
posture), three restrictions exactly match the threat model
(no competing managed service, no key circumvention, no notice
removal).

### Open question this amendment does NOT resolve

The hardware-binding + "agent passport" architecture Alex floated
during the license review is a separate ADR. The substrate is
already shipped (ADR-003X K6 hardware fingerprint, ADR-0049
ed25519 keypair); the passport itself — a Forge-service-signed
certificate that authorizes an agent to roam from its birth
machine — is its own design work. File as ADR-0061 when work
begins.

### Cascading deliverables

- `LICENSE` — replaced (this burst)
- `LICENSE.history` — new (this burst)
- `pyproject.toml` license field — updated (this burst)
- `README.md` license line — updated (this burst)
- `CONTRIBUTING.md` — relicense grant added (this burst)
- `STATE.md` — ADR-0046 entry updated (this burst)
- GitHub repo topics — `open-source-agents` removed; `source-available` added (this burst, via Chrome MCP)
