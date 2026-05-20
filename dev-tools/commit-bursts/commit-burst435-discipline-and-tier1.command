#!/usr/bin/env bash
# Burst 435 — discipline pass + Tier 1 GitHub hardening operator
# helpers + git_local_scan archetype wiring.
#
# This bundles four small changes that all came out of the 2026-05-20
# post-B434 session:
#
#   1. config/tool_catalog.yaml — wire git_local_scan.v1 (B432) into
#      the wiring_sentinel kit. The tool's own catalog entry declares
#      archetype_tags [guardian, observer, security_low,
#      wiring_sentinel, software_engineer] and the description names
#      WiringSentinel as the intended scheduled-task host. Adding it
#      to wiring_sentinel.standard_tools clears the 4th orphan flagged
#      by section-15 of the diagnostic harness (4 -> 3 orphans, the
#      remaining 3 are the known ADR-0081/B393-B399 operator-triage
#      items).
#
#   2. dev-tools/gh-auth-refresh.command (NEW) — one-off helper that
#      runs `gh auth refresh -h github.com -s repo,workflow`. The
#      canonical rotation for the gh-CLI-OAuth-backed credential
#      that actually authorises pushes (per
#      `[credential "https://github.com"] helper = !gh auth git-credential`
#      in ~/.gitconfig). Documents the discovery that the "Forest"
#      classic PAT was dormant — pushes routed through OAuth instead.
#
#   3. dev-tools/enable-ssh-signing.command (NEW) — sets the four
#      git config --global values for SSH-based commit signing:
#      gpg.format ssh, user.signingkey ~/.ssh/id_ed25519.pub,
#      commit.gpgsign true, tag.gpgsign true. Plus a signing smoke
#      test against the key. Pairs with the SSH key uploaded as a
#      GitHub Signing Key + the "main protection" Ruleset's
#      "Require signed commits" rule. After this script runs, the
#      ruleset starts enforcing on the next push.
#
#   4. CLAUDE.md — adds §4 "Test-fixture dataclass field
#      verification" folklore. Three test-fixture API-drift hotfixes
#      in one session (B427 idempotency column names, B429
#      TraitEngine factory, B434 ToolContext fields) make this a
#      clear pattern worth pinning. Section bodies follow the same
#      structure as §1-§3: rule + load-bearing example + how-to-apply.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm:
#     P3 wiring — section-15 flagged git_local_scan.v1 as orphan;
#     B432 shipped a builtin no agent can reach.
#     B6 scripts — Tier 1 hardening was a documented follow-up to
#     B430-B433 ("Operator-side actions still pending"); operator
#     ran the steps and the scripts encode the canonical commands.
#     CLAUDE.md sec4 — three hotfixes in one session is convergent
#     evidence; without a written rule, drift continues.
#   Prove non-load-bearing for kernel:
#     Catalog data + dev-tools scripts + docs. No schema change,
#     no event types, no HTTP routes. Pure userspace per ADR-0044
#     + ADR-0082.
#   Prove alternative is worse:
#     Leaving the changes uncommitted means session deltas drift
#     out of repo into memory only. The commit IS the verification
#     for B6 — it'll be the first signed commit; git log shows G.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 435 — discipline + Tier 1 hardening + kit wiring"
echo "==========================================================="
echo

echo "Pre-commit: clearing stale .git lock files (if any)..."
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null && echo "  cleared" || echo "  none"
echo

echo "Pre-commit signing config (sanity-check before commit):"
echo "  gpg.format        = $(git config --global --get gpg.format 2>/dev/null || echo '(unset)')"
echo "  user.signingkey   = $(git config --global --get user.signingkey 2>/dev/null || echo '(unset)')"
echo "  commit.gpgsign    = $(git config --global --get commit.gpgsign 2>/dev/null || echo '(unset)')"
echo "  expected: ssh / .../id_ed25519.pub / true"
echo "  if commit.gpgsign is unset, run dev-tools/enable-ssh-signing.command first."
echo

git add CLAUDE.md
git add config/tool_catalog.yaml
git add dev-tools/gh-auth-refresh.command
git add dev-tools/enable-ssh-signing.command
git add dev-tools/commit-bursts/commit-burst435-discipline-and-tier1.command

echo "Staged files:"
git diff --cached --stat
echo

git commit -m "feat(governance): discipline + Tier 1 hardening helpers + git_local_scan archetype wiring (B435)

Bundles four small changes from the 2026-05-20 post-B434 session.
All trace back to disciplined follow-up on the prior arc:

(1) config/tool_catalog.yaml — wire git_local_scan.v1 (B432) into
    archetypes.wiring_sentinel.standard_tools. The tool's own
    archetype_tags list and description explicitly name
    WiringSentinel as the intended host. Diagnostic section-15
    orphan count narrows 4 -> 3 (remaining 3 are known
    operator-triage items from ADR-0081/B393-B399). No daemon
    restart needed for the verification because section-15 reads
    the catalog from disk; the running daemon picks up the wiring
    on its next restart, which is fine since WiringSentinel-D5
    isn't born yet.

(2) dev-tools/gh-auth-refresh.command (NEW) — canonical OAuth
    rotation for the gh-CLI-backed credential that actually
    authorises pushes. Discovery: the 'Forest' classic PAT
    (repo,workflow scopes) showed 'Never used' on the GitHub
    settings page despite recent HTTPS pushes, because the active
    credential is the OAuth grant for the 'GitHub CLI' authorized
    app, surfaced via 'helper = !gh auth git-credential' in
    ~/.gitconfig. PAT deleted via web UI; this script handles
    OAuth rotation.

(3) dev-tools/enable-ssh-signing.command (NEW) — sets the four
    git config --global values for SSH-based commit signing
    (gpg.format ssh, user.signingkey ~/.ssh/id_ed25519.pub,
    commit.gpgsign true, tag.gpgsign true) plus runs a signing
    smoke. Pairs with the SSH signing key uploaded via
    github.com/settings/ssh/new and the 'main protection' Ruleset
    created at /settings/rules/new?target=branch with Require
    signed commits enabled.

(4) CLAUDE.md sec4 — Test-fixture dataclass field verification.
    Three hotfixes in one session (B427 idempotency_keys column
    names, B429 TraitEngine.from_yaml + profile_for vs.
    constructor + build_profile, B434 ToolContext
    constitution_path field) all had the same root cause: fixture
    inferred dataclass shape from memory instead of grepping the
    source. New rule: before writing or editing a fixture that
    constructs a kernel dataclass, grep the @dataclass body /
    sqlite schema / factory signature. Same structure as sec1-sec3.

Tier 1 GitHub hardening (ADR-0084) status after this commit:
  1. No Actions without ADR — N/A (no Actions in repo)
  2. Signed commits required on main — LIVE (config + ruleset)
  3. SSH+hardware-key preferred — LIVE (ed25519 signing key)
  4. Branch protection on main — LIVE (Ruleset 'main protection',
     Active, target=Default, Restrict deletions + Require linear
     history + Require signed commits + Block force pushes)
  5. Periodic PAT rotation — closed (dormant PAT deleted;
     OAuth refresh helper landed)
  6. IoC catalog authoritative — already substrate;
     git_local_scan.v1 now wired to consume it on schedule once
     WiringSentinel-D5 births

This commit is the first signed commit on the repo. Verify with
git log format='%h %G? %s' -3 — expect G on this SHA, N on the
previous (everything <=B434 was unsigned).

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: orphan tool unreachable; Tier 1 follow-up uncommitted;
    fixture drift unwritten = 4th hotfix is one session away.
  Prove non-load-bearing for kernel: catalog data + scripts + docs.
    No schema, no event types, no HTTP routes. Userspace per
    ADR-0044 + ADR-0082.
  Prove alternative: leaving uncommitted means session deltas live
    only in memory; rejected because the commit IS the live B6
    verification (first signed commit)." || { echo "commit failed"; exit 1; }

echo
echo "==========================================================="
echo "Commit landed. Signature status of last 3 commits:"
echo "==========================================================="
git log --format='%h %G? %s' -3
echo
echo "Expected: B435 (this commit) shows G (good); B434 + earlier show N."
echo

echo "Pushing to origin (signed-commits rule on main will validate)..."
git push origin main || { echo "push failed — likely the ruleset is rejecting an unsigned commit somewhere in the chain; investigate before retrying"; exit 1; }

echo
echo "Done. B435 landed and pushed."
echo
echo "Press any key to close."
read -n 1 || true
