#!/bin/bash
# Burst 224 — ADR-0055 expansion: reviews, scores, agent templates,
# marketplace auditability.
#
# Operator directive 2026-05-11:
#   "the marketplace can be auditable and people can leave reviews
#    and stars and stuff for individual tools and like scores for
#    skills and agents, and then people can use the templates to
#    create their own tools or clones"
#
# ADR-0055 was at Proposed with M1-M6 covering the substrate
# (index endpoint, signed manifests, sha-pinned install, browse
# pane, grant-to-agent, signing pipeline) and M7 deferred for
# reviews. The directive expands scope without changing the
# Proposed status — the marketplace itself ships from a sibling
# repo, so most delivery is outside the kernel.
#
# Four new decisions appended to the ADR:
#
# D8. Reviews + star ratings. Signed per-review YAML files in the
#     sibling repo under registry/reviews/<entry-id>/<review-id>.yaml.
#     ed25519 signature + reviewer pubkey, single-review-per-key
#     anti-sock-puppet, optional verified-reviewer trust tier,
#     review_count + star_average aggregated server-side in the
#     index response, lazy GET /marketplace/reviews/<entry-id>
#     for full bodies. Off-by-default override
#     (FSF_MARKETPLACE_REVIEWS_ENABLED) for air-gapped /
#     regulated environments.
#
# D9. Skill scoring with two dimensions:
#     - Subjective star ratings (same path as D8 for tools)
#     - Telemetric scores from chain skill_completed /
#       skill_step_failed events, opt-in via
#       FSF_TELEMETRY_REPORT=true. Daemon batches per skill_hash
#       per week + ed25519-signed submission to marketplace's
#       /telemetry/submit. Privacy posture: skill_hash + step
#       counts + success tallies only — NO conversation content,
#       NO agent identities, NO operator identifiers.
#       `fsf telemetry preview` shows the operator exactly what
#       gets sent.
#
# D10. Agent templates as first-class marketplace items.
#      `.template` package shape: template.yaml + soul.md.j2 +
#      constitution.yaml.j2 + recommended_grants.yaml + README.
#      Workflow: browse → Use template → render to editable form
#      → Birth. New audit event agent_birthed_from_template
#      records template id + version + render-time variables for
#      reproducibility. Plus "Clone this agent" sibling action
#      that takes an alive agent's artifacts as the template
#      source — closes "one just like X but tweaked" loop
#      without a marketplace roundtrip. Identity boundary
#      preserved: templates produce NEW DNA, never transplant.
#
# D11. Marketplace auditability via Git commit chain. Per-source
#      commit pinning (FSF_MARKETPLACE_REGISTRIES can include
#      @commit-sha), per-entry change log surfaced in the browse
#      pane, review-staleness flagging (review-for-vN displays
#      "this is vN+1" when the entry advances), signed
#      announcement channel for deprecations / key revocations.
#      Pure UX over Git's existing tamper-evident history; zero
#      new kernel storage. Trust path: kernel
#      marketplace_plugin_installed → registry commit → manifest
#      signature → maintainer key → reviewer attestations.
#
# Tranche table revised:
#   M7 was "deferred" — now concrete reviews + ratings spec.
#   M8 NEW — telemetric skill scores.
#   M9 NEW — agent templates + clone workflow.
#   M10 NEW — marketplace auditability UX.
#
# Status stays Proposed. Implementation begins after this turn,
# starting with the universality unlock B225 (HTTP transport in
# mcp_call.v1) which is independent of the marketplace work.
#
# Per ADR-0001 D2: ADR-0055 explicitly preserves DNA/constitution
#                  immutability. Templates produce new DNA;
#                  installs don't mutate existing agents'
#                  constitution_hash; grants ride the existing
#                  ADR-0060 path.
# Per ADR-0044 D3: pure design doc — zero ABI changes.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0055-agentic-marketplace.md \
        dev-tools/commit-bursts/commit-burst224-adr-0055-expansion.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(adr-0055): reviews, scores, agent templates, audit (B224)

Burst 224. Per operator directive 2026-05-11, ADR-0055 expanded
with four new decisions and four new implementation tranches.

D8: Reviews + star ratings. Signed YAML files in sibling repo.
    Anti-sock-puppet via one-key-one-review per entry.
    review_count + star_average aggregated in index response.

D9: Skill scoring — subjective star ratings PLUS telemetric
    scores derived from opt-in batched chain events.
    Privacy posture: skill_hash + step counts only;
    no content, no identities. fsf telemetry preview for
    operator transparency.

D10: Agent templates. .template package shape with
     soul.md.j2 + constitution.yaml.j2 + recommended_grants.
     New agent_birthed_from_template audit event.
     Clone-this-agent action templated from an alive agent.
     New DNA always; never transplant identity.

D11: Marketplace auditability via Git commit chain.
     Per-source commit pinning, per-entry change-log surfacing,
     review-staleness flagging, signed announcement channel.
     Zero new kernel storage; pure UX over Git's tamper history.

Tranches revised:
  M7 — reviews + ratings (was deferred; now concrete spec)
  M8 — telemetric skill scores (NEW)
  M9 — agent templates + clone (NEW)
  M10 — marketplace auditability UX (NEW)

Status stays Proposed. Implementation begins with the
universality unlock (HTTP transport in mcp_call.v1) which is
orthogonal to the marketplace work.

Per ADR-0001 D2: DNA + constitution_hash immutability
                 preserved; templates produce new DNA.
Per ADR-0044 D3: pure design doc — zero ABI changes."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 224 complete ==="
echo "=== ADR-0055 expanded with D8-D11 + M7-M10. Universality unlock (B225) queued. ==="
echo "Press any key to close."
read -n 1
