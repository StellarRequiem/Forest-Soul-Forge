#!/bin/bash
# Burst 243 — ADR-0049 T4: birth-time ed25519 keypair generation.
#
# Every newly-born agent now carries a unique ed25519 keypair.
# Private key in the AgentKeyStore; public key in BOTH the
# soul.md frontmatter AND the agents.public_key column. The two
# copies must agree at rebuild-from-artifacts time.
#
# Schema bump v18→v19: ALTER TABLE agents ADD COLUMN public_key
# TEXT NULL. Legacy pre-v19 agents stay NULL; their chain
# entries become 'legacy unsigned' under the ADR-0049 D5
# verifier rule (passes hash-chain check, skips signature
# check — T6 surface).
#
# T5 (sign-on-emit) is the natural next tranche — uses the
# private key from AgentKeyStore + the entry_hash to produce
# the signature attached to each agent-emitted chain event.
#
# Files touched:
#
# 1. src/forest_soul_forge/registry/schema.py
#    SCHEMA_VERSION 18→19. agents table CREATE TABLE in
#    DDL_STATEMENTS gains the public_key TEXT column.
#    MIGRATIONS[19] adds the column via ALTER TABLE.
#
# 2. src/forest_soul_forge/registry/ingest.py
#    ParsedSoul gains public_key: str | None. parse_soul_file
#    pulls the optional public_key from frontmatter.
#
# 3. src/forest_soul_forge/soul/generator.py
#    SoulGenerator.generate() accepts public_key keyword.
#    _emit_frontmatter writes 'public_key: "<base64>"' when
#    non-None. Omitted on legacy-style generations.
#
# 4. src/forest_soul_forge/registry/tables/agents.py
#    _insert_agent_row INSERT-s the soul.public_key into the
#    new column.
#
# 5. src/forest_soul_forge/daemon/routers/writes/birth.py
#    _perform_create (shared by /birth and /spawn) generates
#    the Ed25519PrivateKey inside the write lock, stores
#    private via AgentKeyStore.store(), passes public bytes
#    (base64-encoded) into SoulGenerator. The keystore write
#    happens BEFORE any artifact write so a keystore failure
#    aborts birth cleanly.
#
# 6. tests/unit/test_registry.py
#    test_v10_to_v11_forward_migration setup extended to drop
#    the v16-v19 additions (memory_procedural_shortcuts,
#    agent_catalog_grants, agents.public_key) so MIGRATIONS
#    forward through to v19 run cleanly against the v10-shape
#    fixture. Schema version assertions bumped 18→19.
#
# 7. tests/unit/test_plugin_grants.py + test_procedural_shortcuts.py
#  + test_daemon_readonly.py
#    Literal schema_version == 18 assertions bumped to 19.
#    test_v15_to_v16_upgrade fixture extended to drop the
#    v17 catalog_grants table + v19 public_key column.
#
# 8. tests/unit/test_daemon_writes.py
#    New TestBirthGeneratesKeypair class with 6 tests:
#      writes_pubkey_to_soul_frontmatter
#      writes_pubkey_to_agents_table
#      soul_pubkey_matches_agents_row_pubkey
#      private_key_stored_in_agent_keystore
#      each_birth_yields_distinct_keypair
#      birth_pubkey_is_valid_ed25519
#
# 9. docs/decisions/ADR-0049-per-event-signatures.md
#    Status block reflects T1+T4 shipped. Tranche table marks
#    T4 DONE B243 with implementation detail.
#
# Test verification (sandbox):
#   Birth-related suite: 12 passed (6 pre-existing + 6 new T4)
#   Registry + plugin_grants + procedural_shortcuts + readonly
#     + agent_key_store + daemon_plugin_grants: 143 passed
#   Batch B (40 files): 966 passed
#   Integration: 12 passed
#   Zero B243-caused failures.
#
# Per ADR-0001 D2: agent identity now includes (DNA,
#   constitution_hash, public_key) per ADR-0049 D1. The keypair
#   is bound at birth — immutable for the agent's lifetime,
#   matching the other identity components.
# Per ADR-0044 D3: additive — new optional schema column +
#   new optional frontmatter field + new SoulGenerator kwarg.
#   Legacy pre-v19 agents survive untouched (NULL public_key).
# Per CLAUDE.md Hippocratic gate: no removals; existing soul.md
#   files reparse cleanly because the public_key field is
#   optional.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/registry/schema.py \
        src/forest_soul_forge/registry/ingest.py \
        src/forest_soul_forge/soul/generator.py \
        src/forest_soul_forge/registry/tables/agents.py \
        src/forest_soul_forge/daemon/routers/writes/birth.py \
        tests/unit/test_registry.py \
        tests/unit/test_plugin_grants.py \
        tests/unit/test_procedural_shortcuts.py \
        tests/unit/test_daemon_readonly.py \
        tests/unit/test_daemon_writes.py \
        docs/decisions/ADR-0049-per-event-signatures.md \
        dev-tools/commit-bursts/commit-burst243-adr0049-t4-keypair-birth.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(security): ADR-0049 T4 birth-time ed25519 keypair (B243)

Burst 243. ADR-0049 T4 — every newly-born agent now carries a
unique ed25519 keypair. Private in the AgentKeyStore; public in
soul.md frontmatter AND agents.public_key column. The two copies
must agree at rebuild-from-artifacts time.

Schema bump v18 to v19: ALTER TABLE agents ADD COLUMN public_key
TEXT NULL. Legacy pre-v19 agents stay NULL; verifier (T6) will
treat their chain entries as 'legacy unsigned' per ADR-0049 D5.

Birth pipeline (writes/birth.py::_perform_create, shared by
/birth and /spawn) generates Ed25519PrivateKey inside the write
lock, stores private via AgentKeyStore.store() BEFORE any
artifact write so a keystore failure aborts cleanly. Public
bytes base64-encoded and threaded through SoulGenerator.generate
(public_key=...) into the frontmatter; ingest path
(ParsedSoul.public_key → _insert_agent_row) lands the same value
in the agents column.

Tests: 6 new under TestBirthGeneratesKeypair covering
frontmatter write, agents column write, agreement, keystore
fetch, distinct-per-agent, ed25519 round-trip validity.

Test results (sandbox):
  Birth suite: 12 passed (6 pre-existing + 6 new)
  Registry + plugin_grants + procedural_shortcuts + readonly
    + key_store + daemon_plugin_grants: 143 passed
  Batch B (40 files): 966 passed
  Integration: 12 passed
  Zero B243-caused failures.

Per ADR-0001 D2: identity now (DNA, constitution_hash,
  public_key) per ADR-0049 D1; immutable per agent lifetime.
Per ADR-0044 D3: additive — new optional column + frontmatter
  field + SoulGenerator kwarg. Legacy souls reparse cleanly.
Per CLAUDE.md Hippocratic gate: no removals."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 243 complete ==="
echo "=== ADR-0049 T4 keypair generation live. T5 (sign-on-emit) queued. ==="
echo "Press any key to close."
read -n 1
