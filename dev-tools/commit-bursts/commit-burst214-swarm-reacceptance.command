#!/bin/bash
# Burst 214 — ADR-0033 Security Swarm re-acceptance smoke.
#
# The Phase E acceptance smoke fired 2026-04-28. Chain had zero
# security_swarm or canonical-chain events for the two weeks since.
# B214 fired the canonical chain again on the post-B212 daemon and
# fixed two latent bugs in the bringup scripts surfaced during the
# pass.
#
# Bug 1 — auth() function quoting. All three swarm scripts used:
#     auth() { [[ -n "$TOKEN" ]] && echo "-H X-FSF-Token: $TOKEN" ...
#     curl ... $(auth) ...    # unquoted, word-splits
#   curl saw three positional args (`-H` / `X-FSF-Token:` / `<token>`)
#   instead of two (`-H` / `X-FSF-Token: <token>`). Header value was
#   empty; token leaked into the next positional slot. This
#   accidentally worked while the daemon didn't enforce tokens; B148
#   tightened auth and every birth now 401s. Fixed by switching to a
#   proper bash array:
#     declare -a AUTH_HEADER=()
#     [[ -n "$TOKEN" ]] && AUTH_HEADER=(-H "X-FSF-Token: $TOKEN")
#     curl ... "${AUTH_HEADER[@]}" ...
#
# Bug 2 — no .env autoload. Operators expect double-clicking
#   swarm-bringup.command to "just work" without manually exporting
#   FSF_API_TOKEN. The scripts already honored the env var if set,
#   they just didn't read it from the repo .env. Each of the three
#   scripts now has an autoload block:
#     if [[ -z "$TOKEN" ]]; then
#       ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/.env"
#       [[ -f "$ENV_FILE" ]] && TOKEN="$(grep ^FSF_API_TOKEN= "$ENV_FILE" | cut -d= -f2)"
#     fi
#
# With both fixes the canonical chain fired clean:
#   - 9 swarm agents born (seq 7594-7602)
#   - 21 skills reloaded
#   - synthetic incident smoke: 3 agent_delegated hops at seqs
#     7612 (log_lurker → anomaly_ace),
#     7621 (anomaly_ace → response_rogue),
#     7630 (response_rogue → vault_warden)
#   - chain terminated cleanly at vault_warden
#   - 50 events total across seqs 7593-7643
#
# ADR-0033 updated with the new evidence date + seq range + the bug
# fixes recorded in the audit-trail header.
#
# Files touched:
#   swarm-bringup.command                         (.env autoload, top-level)
#   scripts/security-swarm-birth.sh               (.env autoload + bash array)
#   scripts/security-swarm-install-skills.sh      (.env autoload + bash array)
#   scripts/security-smoke.sh                     (.env autoload + bash array)
#   docs/decisions/ADR-0033-security-swarm.md     (re-acceptance evidence header)
#
# What we deliberately did NOT do:
#   - Keep the swarm running on a schedule. That's gated on the
#     Mac mini 24/7 launchd recipe (per memory), which is its own
#     arc. For now the swarm is alive in the registry but not on
#     a polling timer — operators re-fire via swarm-bringup or
#     scripts/security-smoke directly.
#   - Write a new audit doc. The re-acceptance evidence is in the
#     ADR header + the chain itself. A formal audit doc is
#     appropriate when the chain output materially changes
#     (e.g., new tier added, escalation logic rewritten) — this
#     pass was a fidelity check, not a redesign.
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: zero ABI changes — pure script + docs work.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add swarm-bringup.command \
        scripts/security-swarm-birth.sh \
        scripts/security-swarm-install-skills.sh \
        scripts/security-smoke.sh \
        docs/decisions/ADR-0033-security-swarm.md \
        dev-tools/commit-bursts/commit-burst214-swarm-reacceptance.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(swarm): re-acceptance smoke + auth-header quoting fix (B214)

Burst 214. ADR-0033 Phase E acceptance fired 2026-04-28; the chain
had zero canonical-chain events for the two weeks since. B214 re-
fired the chain on the post-B212 daemon and fixed two latent bugs
in the bringup scripts that the run surfaced.

Bug 1 — auth() word-split. All three swarm scripts used
\\\\\`auth() { echo \\\"-H X-FSF-Token: \\\$TOKEN\\\"; }\\\\\` + \\\\\`\\\$(auth)\\\\\`. The
unquoted command substitution word-split into three positional args;
curl saw header name 'X-FSF-Token:' with empty value. Worked pre-B148
when the daemon didn't enforce tokens; fails 401 post-B148. Fixed
with a proper bash array \\\\\`AUTH_HEADER=(-H \\\"X-FSF-Token: \\\$TOKEN\\\")\\\\\`
passed quoted to curl.

Bug 2 — no .env autoload. Scripts honored FSF_API_TOKEN env var but
didn't read it from the repo .env; double-clicking swarm-bringup
failed for operators who hadn't manually exported. Each script now
autoloads from the .env block at the repo root.

Re-acceptance: canonical chain fired end-to-end. 9 blue-team agents
born, 21 skills reloaded, synthetic incident smoke produced 3
agent_delegated hops:
  seq 7612  log_lurker -> anomaly_ace      (morning_sweep matched)
  seq 7621  anomaly_ace -> response_rogue  (match_count >= threshold)
  seq 7630  response_rogue -> vault_warden (post-incident key state snap)
50 events total across seqs 7593-7643; chain terminated cleanly at
vault_warden.

ADR-0033 header updated with the new evidence date + seq range +
the bug-fix record.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: zero ABI changes — script + docs work."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 214 complete ==="
echo "=== Security Swarm re-accepted. Canonical chain alive on the post-B212 daemon. ==="
echo "Press any key to close."
read -n 1
