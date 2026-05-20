# ADR-0084 — GitHub Push-Pipeline Posture

**Status:** Accepted (2026-05-19, B431)
**Date:** 2026-05-19
**Tracks:** Security / Operator hygiene / Supply chain
**Supersedes:** none (extends ADR-0046 license posture, ADR-0062
supply-chain scanner, ADR-0049 per-event signing)
**Builds on:** ADR-0082 (kernel freeze posture — this ADR is a
USERSPACE policy document, not a kernel addition)
**Unblocks:** explicit defense-in-depth against the May 2026
GitHub-incident wave (Grafana Labs, MoneyForward, TeamPCP,
CVE-2026-3854)

## Context

The May 2026 GitHub-incident wave surfaced three distinct attack
patterns that single-operator open-source projects need to defend
against:

1. **GitHub Actions misconfiguration** (Grafana Labs, 5/16/2026):
   `pull_request_target` workflow allowed a forked-repo attacker
   to inject curl, dump env vars encrypted with the attacker's
   private key, exfiltrate the GITHUB_TOKEN, and download the
   private codebase. Grafana refused the ransom demand citing
   FBI guidance.
2. **Hardcoded secrets in commits** (MoneyForward, 5/01/2026):
   370 card records + service credentials accidentally committed
   to GitHub during a service update.
3. **CI credential theft** (TeamPCP attack on Checkmarx,
   3/2026): stolen GitHub Actions CI credentials used to push
   tampered builds.

Plus, **CVE-2026-3854** (disclosed 4/28/2026) was a GitHub
server-side RCE via `git push`. Patched within 2 hours of
bug-bounty disclosure with no exploitation in the wild
confirmed by forensic investigation. Informational for our
posture; not an action item.

Forest-Soul-Forge's current posture against each:

| Vector | FSF state | Action |
|---|---|---|
| Grafana `pull_request_target` | **No GitHub Actions in repo** | Codify "no Actions" as a feature |
| MoneyForward hardcoded secrets | **`.gitignore` covers `.env`; no committed secrets found** | Add IoC rules to security_iocs.yaml (done in B430) |
| TeamPCP stolen CI credentials | **No CI** | Same as Grafana — codify no-CI |
| CVE-2026-3854 | **No exposure** (GitHub patched) | INFO IoC rule for awareness (done in B430) |

**The accidental absence of GitHub Actions is FSF's strongest
defense against the May 2026 wave.** This ADR converts that
accident into an explicit, documented, ADR-grade posture.

## Decision

Forest-Soul-Forge adopts the following GitHub Push-Pipeline
posture as part of its security baseline. Each rule lists its
enforcement layer (operator action, repo configuration, or
substrate code).

### Rule 1: No GitHub Actions without an ADR

**No `.github/workflows/` directory shall exist in FSF main
branch.** Any future workflow addition requires an ADR
documenting (a) the workflow's purpose, (b) the secrets it
needs, (c) the fork-PR threat model, (d) whether it uses
`pull_request_target` and why.

**Enforcement:**
- IoC rule `github_actions_pull_request_target` (ADR-0062 v2,
  B430) flags any commit introducing the dangerous event family.
- IoC rule `github_actions_run_from_fork_unchecked` flags
  unsafe checkout-from-fork SHAs.
- ADR-0082 freeze posture: a Forest distribution that DOES want
  CI must file an ADR with the threat model. SoulUX
  distribution-specific CI lives under `apps/desktop/` or `dist/`
  (userspace per ADR-0044 boundary doc), not under `.github/`.

### Rule 2: Signed commits required on `main`

**All commits to `main` shall be cryptographically signed.**
Either GPG or ed25519 SSH signing key. Unsigned commits
should be rejected by branch protection.

**Rationale:** if the operator's GitHub PAT is stolen, an
attacker can push commits impersonating the operator with
no detection in commit log alone. Signed commits + branch
protection requiring "verified" mean a stolen PAT no longer
enables silent history rewrites.

**Enforcement:**
- Operator-side `git config commit.gpgsign true` +
  `git config gpg.format ssh` + key uploaded to GitHub as
  "signing key."
- Repo-side branch protection rule on `main`: require signed
  commits.

### Rule 3: SSH+hardware-key push preferred over HTTPS+PAT

**Operator preference: push to `origin` via SSH with a
hardware-key-backed ed25519 key (Yubikey, secure enclave,
etc.).** HTTPS+PAT is acceptable but represents a larger
threat surface (PAT can be extracted from keychain by
malware; hardware key cannot be exfiltrated by software).

**Rationale:** the May 2026 wave's exfil vector was always a
TOKEN — env-dump-via-curl in CI (Grafana), or credentials in
committed file (MoneyForward), or stolen CI runner tokens
(TeamPCP). A hardware-key-backed SSH key has no software-
extractable form. Even keychain compromise yields nothing
without the physical key.

**Enforcement:** operator preference, not enforced at the repo
level. Documented as canonical posture; operators choose.

### Rule 4: Branch protection on `main`

`main` branch shall have GitHub branch protection enabled with:

- Require signed commits (Rule 2)
- Require linear history (no force-push)
- Restrict who can push (limit to operator + any explicitly
  invited collaborators)
- Optional: require PR review before merge (only applies once
  collaborators exist; not needed for solo-operator phase)

**Rationale:** branch protection is the last line of defense
between a stolen credential and the canonical repo state.
Without it, a stolen PAT enables silent force-push that
rewrites history; with it, force-push is rejected at the
server.

**Enforcement:** GitHub web UI `Settings → Branches → Add rule`.

### Rule 5: Periodic PAT rotation

**Operator shall rotate the GitHub PAT used for push at least
quarterly.** Rotation is also triggered after any of:

- Known credential exposure (laptop loss/theft, suspected
  malware, public CVE affecting a tool with PAT access).
- ADR-0062 supply-chain scanner finds a GitHub token leak
  pattern (rule `github_pat_or_app_token_committed`).
- Any unexplained push appears in `git log`.

**Enforcement:** operator calendar/process. Not automated.

### Rule 6: ADR-0062 IoC catalog is the authoritative signature set

The supply-chain scanner consulting `config/security_iocs.yaml`
(catalog v2 as of B430) is the authoritative pattern set for
GitHub-related threats. New incidents reported publicly should
trigger a new IoC rule + catalog version bump within one burst
of operator awareness.

**Rationale:** the catalog is part of the codebase, reviewable
in `git log`, version-controlled, and runs locally — no
dependency on a third-party threat-intel feed that could itself
be compromised. ADR-0062's "in-repo IoC catalog" framing is
strengthened by treating it as the canonical GitHub-threat
signature library.

**Enforcement:** ADR-0062 supply-chain scanner runs at install
time on every plugin/tool/skill manifest; B432 adds the
periodic `git_local_scan.v1` builtin that scans the repo
itself against the catalog.

## What this ADR does NOT do

- It does NOT modify any of the seven ABI surfaces (KERNEL.md)
  or seven frozen abstractions (ADR-0082). This is userspace
  policy + IoC catalog content, not kernel surface change.
- It does NOT prevent any Forest distribution from using
  GitHub Actions — the rule is "ADR required before adding,"
  not "forbidden forever."
- It does NOT prescribe specific PAT-rotation tooling. Some
  operators prefer `gh auth refresh`, others prefer manual
  Settings → Developer settings → Tokens. Both are fine
  provided the rotation happens.
- It does NOT change FSF's source-available license (ADR-0046)
  or repo visibility. ELv2 source-available means the codebase
  is intentionally public; the "stolen private code" failure
  mode (Grafana, MoneyForward) doesn't have the same blast
  radius for FSF.

## ADR-0082 compliance check

Per ADR-0082, kernel additions require one of three triggers.
This ADR is **not a kernel addition** — it's a userspace policy
document plus catalog content (the catalog itself is kernel-
adjacent config, not kernel code). The trigger discipline
doesn't apply because no kernel surface changes.

The B430 IoC catalog update IS catalog content (kernel-adjacent
per ADR-0044 boundary doc); adding rules is the same kind of
operation as adding new genres or new skills — additive
configuration, not kernel growth.

The B432 follow-on (`git_local_scan.v1` builtin tool) DOES
add a new builtin tool. Per ADR-0082's "What WOULD be a kernel
addition" list: "A new top-level subsystem under
`src/forest_soul_forge/`" is a kernel addition, but
**adding a new tool to the existing `tools/builtin/` package**
is the canonical extensible-userspace operation that the tool-
registry contract exists for. The new tool follows the existing
Tool Protocol, is registered in the existing catalog, and
exercises existing dispatch + audit-chain infrastructure. Not a
freeze-busting move.

If a future Forest distribution finds an attack pattern that
DOES require a kernel surface change (e.g., a new audit event
type for git-related events that participates in the chain
hash), THAT requires an ADR-0082 architectural-bug-discovery
trigger ADR. This ADR explicitly does not include that work.

## Consequences

**Positive:**

- Converts FSF's accidental "no Actions" state into a documented,
  ADR-grade posture. External assessments (the kind ChatGPT
  delivered 2026-05-19) can now reference ADR-0084 as evidence
  of the discipline rather than reading the absence of
  `.github/workflows/` as accident.
- The May 2026 GitHub-incident wave becomes IoC-catalog-codified
  via B430. New incidents land as new catalog versions.
- Operator-side rules (2-5) give Alex a written checklist for
  account hardening. Each rule has a specific enforcement
  surface (operator action vs. repo config vs. substrate code).
- Reinforces ADR-0049's "audit chain is tamper-PROOF" guarantee
  by establishing that even GitHub-side compromise doesn't
  compromise the canonical record — the local audit chain
  remains the source of truth and the registry is rebuildable
  from it.

**Negative:**

- Operator rules (2-5) require operator action; they don't
  enforce themselves. If the operator skips signed-commit setup
  or branch-protection enrollment, this ADR's value drops
  significantly.
- The IoC catalog patterns (B430) trigger on file PATTERNS, not
  account state. A stolen PAT used to push a clean commit
  bypasses the IoC scanner entirely. Defense-in-depth requires
  the operator-side rules to also be enforced.
- No automated monitoring of GitHub-side state (branch
  protection rules, PAT age, OAuth grants). A future ADR could
  add a periodic `gh api` poller; out of scope here.

**Mitigations:**

- B432's `git_local_scan.v1` builtin runs the IoC scanner +
  basic git-state checks (unsigned commits, sync state, gitignore
  completeness) periodically. Scheduled task makes the rules
  self-checking.
- The operator-side rules are documented checklists. Future
  ADR work could automate verification (e.g., a script that
  checks `gh api repos/owner/repo/branches/main/protection`).

## Implementation status

| Rule | Layer | Status as of B431 |
|---|---|---|
| 1. No GitHub Actions | repo + IoC | ENFORCED — no workflows in repo; IoC rules in B430 |
| 2. Signed commits | operator + repo | OPERATOR ACTION pending |
| 3. SSH+hardware-key | operator | OPERATOR PREFERENCE — not currently enforced |
| 4. Branch protection | repo (GitHub UI) | OPERATOR ACTION pending |
| 5. Periodic PAT rotation | operator process | OPERATOR ACTION pending |
| 6. IoC catalog authoritative | catalog | LIVE — v2 ships in B430 |

The "OPERATOR ACTION pending" rows are not blocked by this ADR;
they're documented as the operator's queue. They will move to
ENFORCED as Alex completes them.

## Open questions

- **Q1: Should an FSF builtin tool periodically `gh api`-poll the
  branch-protection state on `main` and report drift?** Probably
  yes; out of scope for B431. Candidate ADR-0085 if pursued.
- **Q2: Should we add a pre-push git hook that runs the IoC
  scanner against the commit being pushed?** Reduces the
  windows-of-exposure for committed-secret leaks. Out of scope
  for this ADR but a candidate enhancement.
- **Q3: Does FSF need a "trusted publisher" attestation for the
  releases tagged in `dist/`?** The SoulUX distribution may want
  Sigstore-style attestations. Out of scope here; relates to
  the deferred Sigstore C5 work from ADR-003X.

## References

- ADR-0046 — License posture (ELv2 source-available framing
  that contextualizes the "stolen code" damage assessment)
- ADR-0049 — Per-event signatures (audit chain tamper-PROOF
  guarantee that backstops a GitHub-side compromise scenario)
- ADR-0062 — Supply-chain IoC catalog (the substrate this
  ADR adds rules to)
- ADR-0082 — Kernel freeze posture (this ADR is userspace
  policy, not kernel addition)
- ADR-0044 — Kernel/userspace boundary (locates this ADR
  correctly in the boundary map)
- ADR-0025 — Threat model v2 (placeholder; B433 promotes to
  Accepted with this ADR's findings folded in)
- [SecurityWeek — Critical GitHub Vulnerability Exposed Millions of Repositories](https://www.securityweek.com/critical-github-vulnerability-exposed-millions-of-repositories/)
- [Wiz Blog — CVE-2026-3854 breakdown](https://www.wiz.io/blog/github-rce-vulnerability-cve-2026-3854)
- [GitHub Blog — Securing the git push pipeline](https://github.blog/security/securing-the-git-push-pipeline-responding-to-a-critical-remote-code-execution-vulnerability/)
- [The Hacker News — Grafana GitHub Token Breach](https://thehackernews.com/2026/05/grafana-github-token-breach-led-to.html)
- [GBHackers — Grafana Labs confirms security incident](https://gbhackers.com/grafana-labs-confirms-security-incident-github-codebase-access/)
- [Pasquale Pillitteri — MoneyForward GitHub Hack 2026](https://pasqualepillitteri.it/en/news/1842/moneyforward-github-data-breach-2026)
- [The Hacker News — TeamPCP attacks Checkmarx GitHub Actions](https://thehackernews.com/2026/03/teampcp-hacks-checkmarx-github-actions.html)
