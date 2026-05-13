#!/bin/bash
# Burst 245 — License relicense from Apache 2.0 to Elastic License 2.0
# (ELv2). First commit under the new license.
#
# Triggered by Alex's 2026-05-12 review: zero forks + zero
# external production users as of B244 (f799757) was the window
# to switch from Apache 2.0 to a source-available license that
# matches the platform-host business model (hosted Forge SaaS +
# downloadable hardware-bound agents per ADR-003X K6 + per-agent
# ed25519 keypair per ADR-0049).
#
# Choice: ELv2 over BSL/FSL/SSPL/PolyForm. Rationale in ADR-0046
# Amendment 1 (this burst).
#
# Apache-2.0 retroactive coverage: commits through f799757 (B244,
# ADR-0049 fully shipped) are irrevocably Apache 2.0 per Apache
# §4. Anyone who pulled before B245 retains those rights for
# those versions. B245+ is ELv2.
#
# Files touched:
#
# 1. LICENSE
#    Replaced Apache 2.0 text with canonical ELv2 text from
#    elastic.co/licensing/elastic-license, plus a 6-line
#    preamble identifying the Licensor + cutover date pointing
#    at LICENSE.history.
#
# 2. LICENSE.history (NEW)
#    Operator-facing summary: active license is ELv2; commits
#    through f799757 remain Apache 2.0 (irrevocable). Why the
#    change. Effect-of-license table covering common operator
#    scenarios. Future-evolution note.
#
# 3. docs/decisions/ADR-0046-license-and-governance.md
#    Status block: "Accepted 2026-05-05; Amended 2026-05-12
#    (B245) — license switched from Apache 2.0 to ELv2."
#    New Amendment 1 section appended with full rationale:
#    why the original Apache call (kernel-integrator-first
#    posture); what changed (platform-business model + marketplace
#    + zero-fork window); why ELv2 over BSL/FSL/SSPL/PolyForm
#    (comparison table); what does + doesn't change in practice;
#    cascading deliverables list; future ADR-0061 stub for the
#    hardware-binding / passport architecture Alex floated
#    during the license review.
#
# 4. pyproject.toml
#    Classifier flipped "License :: OSI Approved :: Apache
#    Software License" → "License :: Other/Proprietary License"
#    (ELv2 is not OSI-approved). Keywords extended with
#    "source-available" + "elastic-license-v2". Inline comment
#    cites the amendment.
#
# 5. README.md
#    License section rewritten: clear ELv2 framing + three
#    restrictions stated up-front + the carve-out scope (use on
#    own hardware, integration into your own product running
#    on customers' hardware, experimentation + contribution).
#    Pointer to LICENSE.history for cutover context. Telemetry/
#    privacy paragraph preserved.
#
# 6. CONTRIBUTING.md
#    DCO section extended with relicense grant: Signed-off-by
#    trailer = perpetual right to relicense contributions
#    under any future Licensor-chosen license. Preserves
#    flexibility to ever switch back to Apache or MPL without
#    chasing down every individual contributor.
#    "Forking + distributing" section updated for ELv2 framing.
#
# 7. STATE.md
#    ADR-0046 entry: amendment noted with cutover commit ref.
#    The ADRs-filed long line annotates "Amended 2026-05-12 B245
#    — Apache 2.0 → ELv2."
#    License + ethos section rewritten with ELv2 framing.
#
# Per ADR-0001 D2: no identity surface touched (this is a
#   non-code policy change).
# Per ADR-0044 D3: no kernel ABI impact. Conformance suite +
#   external integrator paths are unaffected — ELv2's three
#   restrictions don't prevent integration.
# Per CLAUDE.md Hippocratic gate: Apache rights for pre-B245
#   commits are preserved (Apache's §4 is the mechanism); no
#   removal of existing operator rights.
#
# Next step (separate burst): GitHub repo topics need updating
# via Chrome MCP — remove `open-source-agents`, add
# `source-available` + `elastic-license-v2` if available as
# topics. Documented as deferred to the chrome-tool drive.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add LICENSE \
        LICENSE.history \
        docs/decisions/ADR-0046-license-and-governance.md \
        pyproject.toml \
        README.md \
        CONTRIBUTING.md \
        STATE.md \
        dev-tools/commit-bursts/commit-burst245-elv2-relicense.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "chore(license): relicense Apache 2.0 to ELv2 (B245)

Burst 245. ADR-0046 Amendment 1 — license switched from Apache
2.0 to Elastic License 2.0 (ELv2). First commit under the new
license.

Commits through f799757 (B244 — ADR-0049 tamper-proof chain)
remain irrevocably Apache 2.0 per Apache section 4. Operators
who pulled before B245 retain Apache rights for those versions.
B245 forward is ELv2.

ELv2 over BSL/FSL/SSPL/PolyForm: clean three-restriction shape
(no competing managed service, no key-circumvention, no notice
removal), no time-delay (matches long-running platform business
posture), well-understood by enterprise legal. See ADR-0046
Amendment 1 for the full comparison.

Files: LICENSE replaced; LICENSE.history added documenting the
Apache to ELv2 cutover; ADR-0046 amended; pyproject classifier
flipped to Other/Proprietary; README + CONTRIBUTING + STATE
updated; CONTRIBUTING DCO section adds relicense grant
preserving Licensor flexibility to ever go back to Apache or
similar without chasing individual contributors.

Per ADR-0001 D2: no identity surface touched (policy change).
Per ADR-0044 D3: kernel ABI unaffected.
Per CLAUDE.md Hippocratic gate: pre-B245 Apache rights
  preserved by Apache section 4 irrevocability."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 245 complete ==="
echo "=== Forest Soul Forge is now Elastic License 2.0. ==="
echo "Press any key to close."
read -n 1
