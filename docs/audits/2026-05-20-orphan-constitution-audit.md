# Orphan-constitution audit — 2026-05-20

**Driver:** B443 — long-turn-6 close-out of the "328 orphan
constitution YAMLs" queue item carried forward from older memory.
**HEAD:** `9d41e6b` (B442)
**Run:** `data/test-runs/orphan-constitution-audit-20260520T075000Z/`

## What older memory claimed

Multiple session memories noted "328 orphan constitution YAMLs in
`soul_generated/`, grew from 291" as a housekeeping debt requiring
a careful prune script with audit-chain cross-reference. The
implication: most of those 328 files were dead-letter files left
over from rebirths.

## What the audit found

The new `dev-tools/audit-orphan-constitutions.py` script
classifies every file in `soul_generated/` against two reference
sources: the `agents` table (path + hash) and the audit chain
(all sha256-shaped hex strings).

| Bucket | Count | Disposition |
|---|---:|---|
| LIVE | 37 | Registry agent row references the file. Keep. |
| CHAIN_ONLY | **288** | `constitution_hash` referenced somewhere in audit chain. Keep — chain integrity dependency. |
| ORPHAN | **0** | Safe-to-delete candidates. **None exist.** |
| PARSE_FAILED | 3 | YAML parse fails; already known + handled by harness section-05 as INFO. |

**There are no orphans.** The 288 files I'd been thinking of as
prune candidates are all still chain-referenced. Their hashes
appear in audit chain events — almost certainly the original
`agent_created` events from way back, plus any subsequent
`agent_archived` events that captured the hash for integrity.

Deleting any of the 288 would break the principle established in
ADR-0005/ADR-0006: "the chain is the source of truth; the registry
is rebuildable from it." If you ever needed to rebuild the
registry, you would need the constitution files to verify chain
entries that reference their hashes.

## PARSE_FAILED files

Three files. All match the B369 Kraine/Victor/chaz quarantine
pattern documented in `docs/audits/` history — each has a
hand-appended `# --- override ---` free-text block at end that
breaks YAML parsing while leaving the structural body intact:

- `soul_generated/Kraine__system_architect_054edc592917.constitution.yaml`
- `soul_generated/Victor__knowledge_consolidator_9dd33078e7bd.constitution.yaml`
- `soul_generated/chaz__software_engineer_871a237714a1.constitution.yaml`

Per the B376 quarantine-rebirth lineage record, the AGENT side of
these is resolved (originals archived; new instance_ids minted via
proper birth pipeline). The on-disk FILES persist because their
`constitution_hash` is chain-referenced and removing them would
break verification of the original birth events.

`config/agent_quarantine.yaml` is empty (entries: []) — confirms the
operator-decision side is closed. Section-05 of the diagnostic
harness already reports these 3 as INFO ("constitution parse
health: 3 files failed to parse"), not FAIL.

**No further action needed for these 3 files.** They're a
permanent historical record under the chain's append-only contract.

## Disposition

**Close the queue item.** No prune script needed; no files to
delete. The `audit-orphan-constitutions.py` script ships as
durable tooling — if soul_generated/ grows another 1000 files
across future operator activity, the script will re-run cleanly
and surface any actual ORPHAN candidates that emerge.

## What would change this disposition

- A future ADR introduces explicit constitution-retention policy
  (e.g., "constitution files for agents archived more than 6
  months ago + whose hash hasn't been read from the chain in N
  months can be moved to cold storage"). That would shift some
  CHAIN_ONLY files into a cold-storage bucket. Until such a
  policy lands, the conservative "keep all chain-referenced"
  disposition holds.
- An archive-segmentation pass (ADR-0073) seals old chain segments
  + permits their constitution files to be moved to a parallel
  `soul_generated_archive/` directory. Doesn't apply today;
  parking the idea.

## How to re-run

```bash
python3 dev-tools/audit-orphan-constitutions.py
# or via wrapper (added in a future burst if needed):
# bash dev-tools/audit-orphan-constitutions.command
```

Output lands at
`data/test-runs/orphan-constitution-audit-<ts>/` with
`report.md` (table + sample) + `classifications.json` (full
per-file detail) + `delete-candidates.txt` (empty in the
no-orphan case).

## Cross-references

- `dev-tools/audit-orphan-constitutions.py` — the audit script
- `docs/decisions/ADR-0005-audit-chain.md` — chain-as-source-of-truth
- `docs/decisions/ADR-0006-registry-as-index.md` — registry-rebuildable-from-chain
- `docs/audits/2026-05-17-quarantine-rebirth.md` — B376 lineage record for the 3 PARSE_FAILED entries
- `data/test-runs/orphan-constitution-audit-20260520T075000Z/` — raw run output
