# Full audit — 2026-05-03

Triggered by the audit-chain path mystery surfaced during Run 001
(FizzBuzz autonomous coding-loop smoke test). The script's
post-mortem read `data/audit_chain.jsonl` and reported "0 entries
for the agent" while the daemon was visibly serving requests
elsewhere. That single thread, pulled, exposed multiple silent
drifts. Operator instruction: *no dark corners, no rugs.* This
doc captures every finding from the sweep with severity +
remediation. The companion sentinel `dev-tools/check-drift.sh`
is committed in this same burst so future sessions catch numeric
drift automatically before tagging.

## Method

Numeric claims in `STATE.md` and `README.md` were verified
against the filesystem one by one. Cross-references between
configs (`tool_catalog.yaml`, `genres.yaml`, `trait_tree.yaml`)
were checked. The audit chain at the daemon's actual write path
(`examples/audit_chain.jsonl`) was hash-verified entry by entry.
ADR status fields, CHANGELOG completeness, untracked files, and
zombie test agents in the SQLite registry were enumerated.

## Severity scale

- **P0** — wrong claim that misleads outsiders or breaks tooling
- **P1** — drift that erodes trust in the docs but doesn't break anything
- **P2** — cosmetic / hygiene

---

## A. Numeric drift in published docs (P0)

### Tests passing
| Doc | Claim | Reality |
|---|---:|---:|
| `STATE.md` | 2,072 | 2,072 ✓ |
| `README.md` | 1,968 | 2,072 (-104 stale) |

README is stale by the entire v0.3 ADR-0036 arc (Bursts 65-70 added 104 tests). Should have been refreshed during Burst 81's cross-reference pass.

### Source LoC (Python)
| Doc | Claim | Reality |
|---|---:|---:|
| `STATE.md` | ~36,400 | 44,648 (-8,248 stale) |
| `README.md` | ~46,000 | 44,648 (+1,352 over) |

STATE is most stale — likely written before v0.3 / ADR-0036 / ADR-0040 work. Per-package breakdown:

| Package | LoC |
|---|---:|
| `tools/` | 18,597 |
| `daemon/` | 10,334 |
| `core/` | 5,172 |
| `registry/` | 3,324 |
| `forge/` | 3,117 |
| `cli/` | 1,465 |
| `soul/` | 1,239 |

### ADRs filed
| Doc | Claim | Reality |
|---|---:|---:|
| `STATE.md` | 37 | 37 files, 35 unique numbers ✓ (within 2 amendments) |
| `README.md` | 36 | 37 (-1 stale; missing ADR-0040) |

### Trait roles
| Doc | Claim | Reality |
|---|---:|---:|
| `STATE.md` | 18 | 18 (incl. verifier_loop from ADR-0036) ✓ |
| `README.md` | 17 | 18 (-1 stale; missing verifier_loop) |

### Audit event types
| Doc | Claim | Reality |
|---|---:|---:|
| `STATE.md` | 55 | 55 (claimed) ✓ |
| `README.md` | 52 | 55 (-3 stale; missing verifier_scan_completed + Y-track adds) |

### Skill manifests — naming conflation
- `examples/skills/` — 26 manifests (the canonical "shipped" set)
- `data/forge/skills/installed/` — 23 manifests (the operator-installed subset)

STATE.md's "26 shipped" is correct for the examples count. Missing nuance: only 23 are auto-installed for live runs; 3 remain in examples but not in installed (need to identify which 3 and why). The wording "shipped" vs "installed" should be explicit in both docs.

### .command operator scripts
| Doc | Claim | Reality |
|---|---:|---:|
| `STATE.md` | 36 | 88 (-52 stale; commit-burst*.command files accumulated) |

The `commit-burst<N>.command` files (one per burst) inflate the count. Each is small and single-purpose; they could be archived to a subfolder or rotated, but the count drift itself is the concrete issue.

### Total commits on main
| Doc | Claim | Reality |
|---|---:|---:|
| `STATE.md` | ~155 | 234 (-79 stale) |

The entire v0.3 arc never got reflected. Burst 81 (the cross-reference pass) bumped the LoC narrative but not this number.

### Frontend modules + genres
Both verified ✓ — STATE and README agree, reality matches (22 JS modules, 13 genres).

---

## B. Initiative annotations — STATE claim wrong (P1)

STATE.md says: *"Tools with initiative annotations: 15 of 53."*

Reality:
- `config/tool_catalog.yaml` has `required_initiative_level` on **2** tools (`pip_install_isolated.v1` L4, `memory_flag_contradiction.v1` L3)
- 23 builtin `.py` source files mention `initiative_level` in their code

The annotations were added to **source** during Bursts 46 + 49, but most never propagated to the **catalog YAML**. STATE.md's count appears to conflate source-file presence with catalog presence. The catalog is the configuration of record per ADR-0018.

Remediation: decide where annotations canonically live (catalog vs source), then either backfill the catalog or update STATE to reflect source-side count + explain the split.

---

## C. ADR status inconsistency (P1)

37 ADR files. **None** use a structured frontmatter `Status:` field. Status appears in two patterns:

- `## Status` heading followed by prose (most common)
- Inline mention in body text
- Two ADRs (0035 Persona Forge, 0039 Distillation Forge) still have **placeholder text**: `status: proposed | ratified | rejected | superseded` — never replaced with actual status

Inferred via pattern-grep: 21 accepted-flavor, 12 proposed-flavor, 4 unclear.

Several ADRs labeled "Proposed" are shipped code — STATE.md acknowledges this with parenthetical "(T1-Tn implemented)". From an outside reader's standpoint this looks like documentation drift.

Remediation candidates:
1. Add structured frontmatter (`status:` field) to every ADR
2. Promote shipped-feature ADRs to Accepted (e.g., 0019, 0021, 0022, 0030, 0031)
3. Backfill placeholder status on 0035 + 0039
4. Add an `ADR-INDEX.md` explaining gaps (0009-0015 missing) + status-at-a-glance table

---

## D. CHANGELOG.md missing entire v0.3 arc (P0)

CHANGELOG.md ends with `## [Unreleased]` (empty) and `## [0.2.0]`. The 18 commits between v0.2.0 and HEAD — ADR-0036 Verifier Loop (Bursts 65-70) + ADR-0040 Trust-Surface Decomposition (Bursts 71-81) — have **no CHANGELOG entries**.

This is the natural blocker to tagging v0.3.0: the CHANGELOG should have a `## [0.3.0]` section before the tag lands.

Remediation: write a v0.3.0 section covering both ADR arcs, then tag.

---

## E. Audit chain default path is hidden (P0)

`src/forest_soul_forge/daemon/config.py` defines:

```python
audit_chain_path: Path = Field(
    default=Path("examples/audit_chain.jsonl"),
    description="Audit chain JSONL file.",
)
```

The default points to `examples/audit_chain.jsonl`, not `data/audit_chain.jsonl`. This is non-obvious and bit me during Run 001's post-mortem (script read `data/`, found 0 entries, looked broken). The live chain is at `examples/audit_chain.jsonl` — currently 1083 entries, all hashes verify cleanly.

`README.md` and `STATE.md` never explain this. New contributors (or returning sessions) trying to monitor chain activity will look in the wrong place.

Remediation: add a one-line note to STATE.md's "Things to look up rather than guess" section, and a Conventions row in README.md.

---

## F. Audit chain integrity — VERIFIED (no finding)

All 1083 entries in `examples/audit_chain.jsonl` parse as JSON, have monotonic seq numbers (0..1082), and link via `prev_hash` correctly. No chain breaks, no out-of-order entries. The integrity guarantee is real, not theoretical.

---

## G. Tool catalog ↔ builtin sync — VERIFIED (no finding)

`config/tool_catalog.yaml` has 53 tool entries (dict-keyed by `name.vN`). `src/forest_soul_forge/tools/builtin/` has 53 `.py` files. All names match. **No drift, no orphan tools, no orphan files.** Initial parse was wrong (assumed list vs dict shape) — corrected during the audit.

---

## H. Skill manifest dependencies — VERIFIED (no finding)

All 23 manifests in `data/forge/skills/installed/` declare their `requires:` tool list. Each tool referenced exists at the claimed version in `tool_catalog.yaml`. No broken skills.

---

## I. ADR number gaps (P2)

Missing: ADR-0009, ADR-0010, ADR-0011, ADR-0012, ADR-0013, ADR-0014, ADR-0015 (7 consecutive numbers).

These are intentionally absent from the historical record — likely placeholder slots that were never filled in, or rejected proposals that were withdrawn. There's no document explaining this gap, so an outside reader sees apparent missing work.

Remediation (low priority): a brief paragraph in STATE.md or an ADR-INDEX.md noting "ADR-0009 through ADR-0015 are intentionally absent — placeholder slots from the initial sequencing that were never used."

---

## J. Test-agent zombies in registry (P1)

13 test-fixture agents accumulated in `data/registry.sqlite`:

| Status | Count | Names |
|---|---:|---|
| active | 5 | `Forge_FB001_*` (Run 001 v1-v5 attempts) |
| active | 4 | `VoiceTest` (×3) + `GenreDemo` (×1) |
| archived | 4 | `EngTest_*` (×2) + `RevTest_*` (×2) |

The 5 `Forge_FB001_*` agents are direct fallout from today's FizzBuzz run iterations — none auto-cleaned up after the test ended. The script's post-mortem step did NOT include archive-on-exit logic.

Remediation: archive the 9 active test agents now. Add post-run cleanup to the live-test driver pattern so future runs don't leak.

---

## K. Untracked / uncommitted files (P1)

```
dev-tools/check-drift.sh           # NEW — drift sentinel, commits this burst
live-test-fizzbuzz.command         # NEW — Run 001 driver, commits this burst
examples/audit_chain.jsonl         # MODIFIED — daemon appends; leave alone
```

Plus the test-run outputs at `data/test-runs/fizzbuzz-001/` which are correctly under a `.gitignore`'d path.

---

## L. ADR status field is unstructured (P2)

Cross-cuts with finding C. Worth calling out separately because the **structural** gap is that ADRs lack a machine-readable status field. Any audit script that wants to know "which ADRs are Accepted?" has to grep prose. Adding YAML frontmatter (e.g., `status: accepted`) would make this scriptable and let `dev-tools/check-drift.sh` validate ADR status drift in future runs.

---

## Remediation plan

Single-burst fixes (Burst 83):
- Refresh `STATE.md` with corrected counts (LoC, commits, .command, skill installed/shipped clarification, audit chain path note)
- Refresh `README.md` table (tests, ADRs, trait roles, audit events)
- Write CHANGELOG.md `## [0.3.0]` section covering ADR-0036 + ADR-0040 arcs
- Archive the 9 zombie test agents via host script
- Add audit chain path documentation row in both docs

Multi-burst fixes (queued, not in Burst 83):
- ADR status standardization — frontmatter pass over all 37 ADRs (Burst 84)
- Initiative annotation reconciliation between catalog YAML and source files (Burst 85)
- ADR-INDEX.md with gap explanation + status-at-a-glance (Burst 86)

Future-proofing (this burst):
- `dev-tools/check-drift.sh` runs every numeric check above. CI-style: a one-command sanity check before tagging.
- `live-test-fizzbuzz.command` committed as the canonical driver pattern (5-bug ledger encoded in the comment block) so future scenario runs don't repeat the same mistakes.

## Lessons-learned addenda for CLAUDE.md

Two patterns surfaced that future sessions should know:

1. **`python3 - <<'PYEOF'` makes the heredoc replace stdin.** When you need `sys.stdin.read()` to work on piped input, use `python3 -c '...'` instead. (Surfaced during Run 001 v4 → v5 fix.)
2. **`curl -sf` swallows error response bodies.** When you need to debug a 4xx/5xx, drop `-f` so the body surfaces. The `|| echo '{}'` fallback hides the actual failure. (Surfaced during Run 001 v1 → v2 fix.)

These will land in CLAUDE.md as feedback memories during Burst 83.

---

**Bottom line:** the codebase substance is solid (audit chain verifies, tool/skill consistency holds, tests pass). The drift is in the **documentation surface** that outsiders read first. Closing that gap before tagging v0.3.0 is the right sequence.
