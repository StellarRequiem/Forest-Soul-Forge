# Contributing to Forest

Forest is an agent governance kernel. The contribution discipline
is shaped by that posture: the kernel commits to backward
compatibility on a defined set of surfaces (see `KERNEL.md`), so
every change either preserves those surfaces, refines them through
an ADR, or lives in userspace where breaking changes are routine.

This file is the practical guide for contributors. The strategic
context lives in [`ADR-0044`](docs/decisions/ADR-0044-kernel-positioning-soulux.md)
(kernel positioning) and [`ADR-0046`](docs/decisions/ADR-0046-license-and-governance.md)
(license + governance). Read those if you want to understand the
*why*; this file is the *how*.

## Before you start

1. **Read the boundary doc.**
   [`docs/architecture/kernel-userspace-boundary.md`](docs/architecture/kernel-userspace-boundary.md)
   labels every directory as kernel / userspace / kernel-adjacent /
   operator-state. Knowing which side your change lives on
   determines what bar it has to clear.
2. **Read `KERNEL.md`.** The seven v1.0 ABI surfaces. If your
   change touches one, an ADR usually applies.
3. **Browse recent ADRs.** [`docs/decisions/`](docs/decisions/) is
   the public RFC record. Recent ADRs (0040 trust-surface
   decomposition, 0043 plugin protocol, 0045 trust-light system,
   0044 kernel positioning) show the discipline.

## How to propose a change

### Small kernel-internal refactor (no ABI surface touched)

1. Open a PR.
2. Run the suite + the boundary sentinel:
   ```
   PYTHONPATH=src python3 -m pytest tests/unit -q
   ./dev-tools/check-kernel-userspace.sh
   ```
   Both must pass.
3. Add a clear commit message. ADR-0040 trust-surface
   decompositions are the canonical pattern for "internal refactor
   that preserves public API."
4. Steward (currently Alex) reviews + merges.

### Userspace change (apps/desktop, frontend/, dist/, examples/)

1. Open a PR.
2. Run the suite (most userspace doesn't impact tests, but verify).
3. The bar is lower than kernel work — userspace doesn't bind
   v1.0. Move fast.

### ABI-touching change

1. **File an ADR first** under `docs/decisions/ADR-NNNN-<slug>.md`.
   Use the existing ADRs as a format template. Title, Status:
   Proposed, Context, Decision, Consequences, References.
2. Open the ADR PR alone (no implementation yet). Steward reviews
   the design.
3. After ADR is Accepted, file the implementation PR(s). Reference
   the ADR by number in commit messages.
4. If the ADR needs revision, amend in place (status: Proposed →
   Revised → Accepted) or file an amendment ADR (e.g.,
   `ADR-0021-amendment-genre-initiative-level.md` is the
   precedent).

### Brand-new feature (not a refactor)

1. File an ADR sketching the design. Even a one-page ADR clears
   the air about scope + tradeoffs before code lands.
2. Implementation can decompose into multiple bursts (see Bursts
   95-115 of the v0.5 arc for the canonical example).

## Test discipline

- **Every kernel change ships with tests.** The 2,386 unit tests
  in `tests/unit/` are the conformance baseline. New surfaces add
  tests; refactors preserve them.
- **Run the full suite before submitting.** PRs that introduce
  regressions get bounced.
- **Use the seed_stub_agent fixture** when your test needs an
  agent row in the registry. SQLite FK enforcement is on; tests
  that exercise tables with FK to agents.instance_id MUST seed
  the agent row first. See `tests/unit/conftest.py`.
- **xfail with a specific reason, not skip with vague text.** An
  `xfail` with a documented reason (sandbox SQLite mismatch, etc.)
  is honest; a `pytest.skip(reason="env-mismatch")` masking a real
  bug is not.

## Commit messages

Conventional Commits-style prefixes are used in the repo. Examples
from recent history:

- `feat(plugins): plugin grant substrate (ADR-0043 fu#2)`
- `feat(posture): per-grant trust_tier enforcement (ADR-0045 T3+T4)`
- `docs(adr): ADR-0046 License Posture + Governance`
- `feat(dev-tools): kernel/userspace boundary sentinel`
- `chore(audit): refresh examples/audit_chain.jsonl from test runs`

Reference the ADR number when the commit lands implementation
work. Reference the burst number when work is part of a tracked
arc. Both is best; either is acceptable.

### Signed-off-by (DCO)

Forest uses the [Developer Certificate of Origin](https://developercertificate.org/)
posture rather than a CLA. Every commit should include a
`Signed-off-by: Your Name <your@email>` trailer. Add it
automatically with `git commit -s`. The trailer is your
attestation that you have the right to submit the contribution
under the project's Apache 2.0 license.

## Code of conduct

We follow the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md). Be
respectful, assume good faith, criticize the work not the person.
The steward enforces.

## Filing issues

- **Bug reports** include: reproduction steps, expected behavior,
  actual behavior, daemon version + git SHA, relevant audit chain
  excerpts when the bug touches dispatch / governance.
- **Feature requests** are welcome but get prioritized against the
  kernel-shape positioning. Userspace polish requests usually
  land faster than ABI-touching changes (which need an ADR).
- **Security issues** — please email rather than file a public
  issue if the report involves a privilege-escalation vector or
  audit-chain integrity flaw. Issue tracker is fine for plugin
  protocol or governance-pipeline ergonomics issues.

## Forking + distributing your own thing

You're encouraged to. Apache 2.0 permits commercial forks,
proprietary derivatives, or building your own distribution on top
of the Forest kernel. The conventions Forest reserves:

- **"Forest" as a project name** refers to the kernel as
  maintained by the steward(s). Forks can use the code, but
  please don't call your fork "Forest" — it confuses operators
  about which project is the canonical maintained one.
- **"SoulUX" as a distribution name** refers to the reference
  distribution (the Tauri shell + frontend). Forks can build
  distributions on Forest, but don't call your distribution
  "SoulUX" for the same reason.
- **Kernel ABI conformance** — a distribution that diverges from
  the v1.0 kernel ABI stops being a "Forest distribution." This
  is enforced socially via project recognition + documentation
  cross-references.

These are conventions, not legal restrictions. They become more
load-bearing once trademark posture is filed (deferred per
ADR-0046).

## Contact

Steward: Alex (StellarRequiem on GitHub).

Issue tracker: https://github.com/StellarRequiem/Forest-Soul-Forge/issues
ADR proposals: PR against `docs/decisions/`
Sensitive disclosures: email the steward directly.

## Where to look first as a new contributor

| Question | File |
|---|---|
| What is Forest, strategically? | [`README.md`](README.md), [`ADR-0044`](docs/decisions/ADR-0044-kernel-positioning-soulux.md) |
| What does the kernel commit to? | [`KERNEL.md`](KERNEL.md) |
| Where is the boundary between kernel and userspace? | [`docs/architecture/kernel-userspace-boundary.md`](docs/architecture/kernel-userspace-boundary.md) |
| What's the live state of the codebase? | [`STATE.md`](STATE.md) |
| What's the design history? | [`docs/decisions/`](docs/decisions/) |
| What did we ship recently? | [`CHANGELOG.md`](CHANGELOG.md) |
| Who contributed what? | [`CREDITS.md`](CREDITS.md) |
| What's the license? | [`LICENSE`](LICENSE) (Apache 2.0) |
| What's the conduct standard? | [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) |
