# ADR-0025 — Threat model v2

- **Status:** **Accepted** (2026-05-19, B433 — promoted from placeholder).
- **Date:** 2026-04-27 (placeholder); 2026-05-19 (accepted).
- **Tracks:** Security / Operator hygiene / Substrate guarantees
- **Builds on:** ADR-0005 (v1 threat model in audit chain docstring),
  ADR-0049 (per-event signatures — audit chain now tamper-PROOF for
  agent-emitted events), ADR-0061 (agent passport), ADR-0062
  (supply-chain IoC catalog), ADR-0063 (reality anchor),
  ADR-0082 (kernel freeze posture), ADR-0084 (GitHub
  push-pipeline posture)
- **Triggers when:** This document is now the current threat model
  for Forest-Soul-Forge. Subsequent updates require a new ADR or an
  amendment to this one.

## Why this changed

The original ADR-0025 was a placeholder deferred to v0.3+ pending
federation work. As of 2026-05-19, federation is still not on the
near-term roadmap, BUT three changes since 2026-04-27 forced a real
threat model:

1. **ADR-0049 (B244) per-event signatures shipped.** The audit chain
   moved from tamper-EVIDENT to tamper-PROOF for agent-emitted
   events. The threat model needs to be explicit about what that
   means (and doesn't mean).
2. **Phase α 10/10 substrate ADRs closed.** The kernel surface
   stabilized. The threat model needs to describe what's defended
   at the kernel layer vs. what's operator hygiene.
3. **May 2026 GitHub-incident wave** (Grafana Labs, MoneyForward,
   TeamPCP, CVE-2026-3854). The push-pipeline surface earned its
   own threat ADR (ADR-0084). This document collects the broader
   picture.

The original placeholder's framing — "v1 ships an honest-but-
forgetful threat model" — is preserved as the historical baseline.
v2 (this document) is "operator-machine-trusted, audit-chain-
tamper-proof, GitHub-side-untrusted."

## The current threat model

### Trust boundaries

| Boundary | Trust state | Defense |
|---|---|---|
| Operator's physical machine | TRUSTED | None — root compromise of the box is out of scope (ADR-0005) |
| FSF daemon process | TRUSTED relative to operator | API token gate (B148); single-writer SQLite lock |
| Local audit chain JSONL | TAMPER-PROOF for agent events | ADR-0049 ed25519 per-event signing |
| Local audit chain (operator-emitted events) | TAMPER-EVIDENT | Hash-link chain (ADR-0005) — a root attacker could forge, but the forgery would be visible in chain-rebuild verification |
| Local SQLite registry | Rebuildable from chain | Single-writer lock; canonical state is the chain |
| Local constitution YAMLs | Immutable per agent | Content-addressed `constitution_hash`; ADR-0007 invariant |
| Local secret store | TRUSTED per backend | Pluggable per ADR-0052 (file / keychain / vaultwarden) |
| Operator-installed plugins | UNTRUSTED until vetted | ADR-0043 plugin protocol + ADR-0062 supply-chain IoC scanner + sha256 entry-point pinning |
| Operator-installed forged tools | UNTRUSTED until vetted | ADR-0030/0031 forge pipeline (operator-review gate); per-tool plugin grants (ADR-0053) |
| LLM provider (local Ollama) | TRUSTED at network layer | Localhost-only by default (ADR-0008) |
| LLM provider (frontier) | UNTRUSTED at content layer | Opt-in via FSF_FRONTIER_ENABLED; no cross-tier swarm escalation without operator approval |
| GitHub origin | UNTRUSTED for code state, TRUSTED for distribution | ADR-0084 push-pipeline posture; local audit chain is the canonical record; origin is a publication mirror |
| Network between operator and any third party | UNTRUSTED | TLS for outbound; no inbound listening except localhost daemon |
| Future federation (Horizon 3) | NOT YET MODELED | Deferred to a future ADR-0085 federated-threat-model when federation work begins |

### Adversary categories

The defended-against categories are listed first; out-of-scope
categories are listed at the end.

**Defended:**

1. **Stolen GitHub credentials.** Attacker exfiltrates Alex's
   GitHub PAT or session token via keychain extraction, OAuth
   phishing, or third-party tool compromise. Goal: push tampered
   commits to make tampered FSF distributions available to
   integrators.

   *Defense:* ADR-0084 Rule 2 (signed commits) + Rule 4 (branch
   protection requiring verification) + Rule 5 (PAT rotation).
   The local audit chain is the canonical record; origin
   compromise is detectable by comparing local chain hash against
   the chain hash a fresh clone of origin would produce. ADR-0049
   ed25519 per-event signing means agent activity in the local
   chain is independently verifiable.

   *Cost of success:* attacker can push tampered code to origin,
   which integrators may pull. Repo viewers see "Verified" badge
   ONLY if attacker also stole signing key — without the signing
   key, tampered pushes show as unverified, which branch
   protection rejects. Mitigation chain: attacker needs PAT +
   signing key + branch-protection bypass to silently publish
   tampered code.

2. **Hardcoded-secret-in-commit leak.** Operator accidentally
   commits a real secret (MoneyForward pattern).

   *Defense:* `.gitignore` covers operator-secret-bearing paths
   (B432's gitignore-completeness check verifies this); pre-push
   git_local_scan.v1 invocation (or scheduled hourly run) catches
   committed secrets before they reach origin; ADR-0062 IoC
   catalog v2+ (B430) provides the pattern set. After leak:
   GitHub Secret Scanning + operator-rotation discipline.

3. **Supply-chain attack via installed plugin / forged tool.**
   Malicious plugin manifest or forged tool from the Forge UI
   attempts to RCE the daemon at install time (MCP-STDIO-RCE
   pattern, Anthropic 2026-04 vuln family).

   *Defense:* ADR-0043 plugin protocol sha256 pinning; ADR-0062
   supply-chain IoC scanner at install time with 21+ rules
   covering active 2025-26 attack patterns; ADR-0053 per-tool
   plugin grants (operator must explicitly grant each tool); the
   ADR-0082 kernel freeze means plugins can't widen the substrate
   surface without an ADR.

4. **Cross-agent privilege creep.** Malicious agent (e.g.,
   compromised via prompt injection) attempts to delegate to a
   higher-privileged sibling.

   *Defense:* ADR-0034 triune-bond restriction on delegate.v1;
   per-tool side-effect classification + per-genre kit-tier
   ceiling (ADR-0021); approval queue for `external` side-effects;
   ADR-0045 posture (red dominates per-grant precedence).

5. **Audit chain tampering by a process within the daemon.**
   Daemon-internal logic error or malicious tool tries to forge
   audit entries.

   *Defense:* ADR-0049 per-event signing — agent-emitted events
   carry ed25519 signatures of the canonical body. Tampering
   with the entry breaks signature verification. ADR-0073 audit
   chain segmentation supports operator-time forensics.

6. **GitHub Actions misconfiguration injection (Grafana
   pattern).** Attacker submits a PR that introduces a
   `pull_request_target` workflow to dump env vars + exfiltrate
   the GITHUB_TOKEN.

   *Defense:* ADR-0084 Rule 1 — no GitHub Actions without an
   ADR. ADR-0062 IoC catalog v2 rule
   `github_actions_pull_request_target` (B430) flags any commit
   adding a workflow. The current repo has zero workflows; this
   defense holds by construction.

7. **Prompt injection through observed content** (agent reads
   an attacker-authored document and follows embedded
   instructions).

   *Defense:* The Anthropic-Claude security boundary itself
   (the agent runtime's responsibility, not FSF substrate).
   ADR-0063 reality anchor adds a substrate-level check —
   operator-asserted ground truth catalog + RealityAnchorStep
   in the governance pipeline rejects pre-action claims that
   contradict ground truth.

**Out of scope:**

- **Root-level compromise of the operator's machine.** If
  attacker has root on Alex's Mac, they can read the local
  audit chain, the secret store, the constitutions, etc.
  FSF makes no claim to defend against this. The audit chain
  is tamper-EVIDENT against this (a root attacker can rewrite
  it, but the rewrite is detectable if the operator has
  off-machine chain snapshots), not tamper-PROOF.
- **Nation-state APT.** FSF is a solo-operator open-source
  project. Defense-in-depth helps but no claim of APT
  resistance.
- **Side-channel attacks on the LLM provider.** Frontier
  providers may leak via timing, response-shape, or model
  weights. FSF can't defend against the provider itself.
  Mitigation is "use local Ollama for sensitive work."
- **Operator social engineering.** If Alex is tricked into
  running a malicious commit-burst.command from his email,
  FSF can flag IoC patterns but can't prevent execution.
  Discipline is the only defense; ADR-0084 Rule 6 (IoC
  catalog as authoritative) makes the patterns reviewable.
- **Future federation threats.** Adversarial realm hosts,
  cross-realm identity forgery, federated audit-chain
  anchoring — all deferred to a future ADR (the
  original placeholder's purpose).

### Where the canonical truth lives

The single most important property: **the local audit chain
JSONL is the source of truth, not GitHub.** This is preserved
through every threat scenario above. Origin compromise doesn't
compromise the local audit chain. Stolen PAT can rewrite origin
but can't rewrite the local chain (which the operator's local
copy verifies via hash-chain integrity + ADR-0049 signatures).
A fresh clone of origin + the operator's local-chain reference
hash is sufficient to detect any origin-side tampering.

This is **distinct** from the Grafana-style threat (private
code stolen) and the MoneyForward-style threat (private secrets
leaked):

- FSF source code is ELv2 source-available → no "stolen
  private code" damage; the code is intentionally public.
- FSF doesn't commit secrets (B430 + B432 enforce this) →
  no MoneyForward-style accidental-leak damage.

The remaining damage shape is **tampered distribution**:
attacker pushes tampered code to origin that integrators may
pull. ADR-0084's defenses (signed commits + branch protection)
make this require multiple compromised credentials, which
narrows the attack surface significantly.

### Per-event signing — what it guarantees and doesn't

ADR-0049 shipped per-event ed25519 signing in B244. Every
agent-emitted audit chain event after that burst carries a
non-null `signature` field. The signature covers the canonical
event body. Verification ensures:

- The entry was emitted by an agent with the claimed
  `instance_id` (signature verifies under that agent's
  registered public key)
- The entry body has not been tampered with since signing
- The entry's chain position is consistent (hash-link still
  required)

What it does NOT guarantee:

- That the event REPRESENTS reality (an agent can choose to
  emit a misleading event before signing it — that's a
  prompt-injection / behavior problem, addressed by
  ADR-0063 reality anchor and constitution policy gating,
  not by the signature)
- That OPERATOR-emitted events (birth, archive, etc.) are
  signed — those are NOT covered by ADR-0049's per-event
  signing; they remain tamper-EVIDENT via the hash chain
- That the operator is the right operator (federation problem
  deferred)

### Defense-in-depth principles applied

1. **The kernel is frozen** (ADR-0082). New attack surface
   requires an ADR. The threat surface stops growing
   uncontrolled.
2. **The IoC catalog is in-repo** (ADR-0062). Threat
   patterns are version-controlled; updates are commits;
   no dependency on a third-party threat-intel feed.
3. **The audit chain is canonical** (ADR-0005, ADR-0049).
   Origin is downstream; the local chain is the truth.
4. **Per-tool grants** (ADR-0053). Even an installed plugin
   has no privileges unless the operator explicitly grants
   each tool to each agent.
5. **No CI by default** (ADR-0084). The biggest 2026
   GitHub attack vector (`pull_request_target`) doesn't
   apply.
6. **Localhost-only by default** (ADR-0008). Network
   exposure requires explicit opt-in.

## What this ADR does NOT do

- It does NOT model the federation case. That remains
  deferred (now to a future ADR-0085 or amendment to this
  one when federation work begins).
- It does NOT change any kernel surface. This is a
  documentation update only; ADR-0082 freeze-posture
  compliance is trivial.
- It does NOT add new defenses. Every defense referenced
  is already shipped via the cited ADRs.

## Consequences

**Positive:**

- FSF now has a documented threat model, not a placeholder.
  External assessments (security reviews, integrator
  due-diligence) can reference ADR-0025 as the current
  posture.
- The relationship between local audit chain and GitHub
  is explicit — "GitHub is downstream; local is canonical."
  Resolves prior ambiguity about which copy wins on
  divergence.
- Operator hygiene rules (PAT rotation, signed commits,
  no Actions) have a written threat model justifying them
  rather than reading as folklore.

**Negative:**

- The threat model is honest about what's out of scope.
  Some readers may interpret "root-level compromise out of
  scope" as a weakness. It's not — it's an accurate scope
  statement. FSF would mislead by claiming defense against
  APTs or root attackers.
- Federation is still deferred. A reader looking for
  multi-realm threat modeling won't find it here. That's
  the right deferral.

**Mitigations:**

- ADR-0084 (GitHub push-pipeline) is the operator-side
  companion to this ADR; together they cover the May 2026
  threat surface comprehensively.
- B430's IoC catalog v2 is the substrate that turns the
  threat model into runtime detection.
- B432's git_local_scan.v1 is the local-state self-check
  tool that the operator can schedule hourly.

## Open questions

- **Q1: Should this ADR be re-visited at every Phase
  closure?** Probably yes; threat model drift mirrors
  substrate drift. Suggest re-review at Phase β / γ
  closures.
- **Q2: Should there be a substrate event type
  `threat_model_amendment`?** Useful for auditability of
  threat-model changes but adds an event type to the
  frozen KNOWN_EVENT_TYPES list — ADR-0082 architectural-
  bug-discovery trigger would be needed. Out of scope here.
- **Q3: When federation lands, does this ADR get
  superseded or amended?** Cleaner to supersede — federation
  changes the trust boundary diagram enough that v3 is the
  right next number, not an amendment to v2.

## References

- ADR-0005 — Audit chain (v1 threat model docstring,
  hash-link integrity)
- ADR-0049 — Per-event signatures (tamper-PROOF for agent
  events)
- ADR-0061 — Agent passport (cryptographic identity)
- ADR-0062 — Supply-chain IoC catalog (now at v2 per B430)
- ADR-0063 — Reality anchor (pre-action ground-truth check)
- ADR-0082 — Kernel freeze posture (limits where the
  threat surface can grow)
- ADR-0084 — GitHub push-pipeline posture (operator-side
  companion to this ADR)
- B432 — git_local_scan.v1 builtin tool (the runtime
  self-check that exercises this threat model)
- Memory [[project_2026_05_19_b422_b429_extended_arc]] —
  the discipline arc that motivated this promotion
- [SecurityWeek — CVE-2026-3854](https://www.securityweek.com/critical-github-vulnerability-exposed-millions-of-repositories/)
- [GitHub Blog — Securing the git push pipeline](https://github.blog/security/securing-the-git-push-pipeline-responding-to-a-critical-remote-code-execution-vulnerability/)
- [The Hacker News — Grafana GitHub Token Breach](https://thehackernews.com/2026/05/grafana-github-token-breach-led-to.html)
