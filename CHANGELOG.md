# Changelog

All notable changes to Forest Soul Forge are documented in this file.

Format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/). This project uses [Semantic Versioning](https://semver.org/); until 1.0.0 the API is unstable.

## [Unreleased]

### Added
- Initial repo scaffolding: directory structure, README, LICENSE (Apache 2.0), `.gitignore`, `pyproject.toml`.
- Vision brief preserved in `docs/vision/handoff-v0.1.md`.
- Directory layout rationale in `docs/architecture/layout.md`.
- ADR and audit indexes in `docs/decisions/README.md` and `docs/audits/README.md`.
- Phase 1: hierarchical trait tree design — `docs/architecture/trait-tree-design.md` (5 domains, 10 subdomains, 26 traits, 5 role presets, 7-phase expansion roadmap).
- Phase 1: trait tree schema — `config/trait_tree.yaml`.
- Phase 1: ADR-0001 (hierarchical trait tree with themed domains and tiered weights), status Accepted.
- Phase 2: core engines.
  - `src/forest_soul_forge/core/trait_engine.py` — loads and validates `trait_tree.yaml`, exposes typed API (Trait, Domain, Subdomain, Role, TraitProfile, FlaggedCombination). Includes profile builder, effective-weight calculator, and flagged-combination scanner.
  - `src/forest_soul_forge/soul/generator.py` — converts a TraitProfile into a structured `soul.md`, ordered by effective domain weight, with tier-based trait inclusion and warning surfacing.
  - `tests/unit/test_trait_engine.py` and `tests/unit/test_soul_generator.py` — pytest unit tests. 68 passing via the stdlib harness; awaiting pytest run on the user's machine for full fidelity.
  - `scripts/demo_generate_soul.py` — end-to-end smoke test that generates example `soul.md` files under `examples/` and verifies weight math.
  - `scripts/run_tests_no_pytest.py` — stdlib-only test harness so we can exercise the pytest suite in environments without pytest available.
  - Runtime dep added: `pyyaml>=6.0`.
- Phase 2 remainder: grading engine, constitution builder, audit chain.
  - **Grading engine (ADR-0003)** — `src/forest_soul_forge/core/grading.py`. Pure function `grade(profile, engine) -> GradeReport` computes a config-grade score: subdomain scores are tier-weighted means of trait values, intrinsic domain score is the mean of its subdomains, overall is the role-weighted mean of intrinsic domain scores. Tertiary-tier traits below `TERTIARY_MIN_VALUE` (40) surface as warnings. Dominant domain is selected with a canonical tie-break (security → audit → emotional → cognitive → communication). `GradeReport.render()` produces a CLI-friendly multi-line summary. Frozen dataclasses throughout; fully deterministic. Tests: `tests/unit/test_grading.py` (~20 cases) plus sandbox smoke `scripts/verify_grading.py` (23/23 passing).
  - **Constitution builder (ADR-0004)** — `src/forest_soul_forge/core/constitution.py` + `config/constitution_templates.yaml`. Three-layer composition: `role_base` (per-role policies and thresholds), `trait_modifiers` (threshold-triggered policies, e.g. `caution>=80` adds `caution_high_approval`), `flagged_combinations` (dangerous trait intersections emit `forbid` policies). Conflict resolution is strictness-wins across the ordered set `{allow, require_human_approval, forbid}`; weaker rules keep their entry but record a `superseded_by` pointer. Non-ordered rules like `require_explicit_uncertainty` stack without conflict. `constitution_hash` is SHA-256 over canonical JSON of the rulebook body only — identity (role, DNA, agent name) is bound in the soul frontmatter, not the hash. This lets two agents with the same profile share a constitution hash while keeping distinct DNA. Tests: `tests/unit/test_constitution.py` (~20 cases) plus `scripts/verify_constitution.py` (34/34 passing).
  - **Soul ↔ constitution binding** — `SoulGenerator.generate()` accepts `constitution_hash` and `constitution_file` keyword-only arguments; when supplied, both are emitted into the frontmatter immediately after `generated_at`. Passing exactly one of the pair raises `ValueError` — they are an atomic pair or both absent.
  - **Audit chain (ADR-0005)** — `src/forest_soul_forge/core/audit_chain.py`. Append-only hash-linked JSONL log; SHA-256 over canonical JSON of `{seq, prev_hash, agent_dna, event_type, event_data}` — timestamp is deliberately excluded to keep clock skew from breaking verification. Auto-genesis on open (a fresh file gets a `chain_created` entry synchronously). Eight known event types (`chain_created`, `agent_created`, `agent_spawned`, `constitution_regenerated`, `manual_override`, `drift_detected`, `finding_emitted`, `policy_violation_detected`); unknown event types verify as warnings, not failures, for forward-compat. `verify()` walks from seq=0 and reports the first structural break (seq gap, prev_hash mismatch, entry_hash mismatch, invalid JSON) with the offending seq and reason. v0.1 is **tamper-evident, not tamper-proof** — see ADR-0005 for the threat model. `_recompute_head` tolerates malformed trailing lines on open so `verify()` remains callable against corrupted files. Operator-facing docs at `audit/README.md`. Tests: `tests/unit/test_audit_chain.py` (~24 cases) plus `scripts/verify_audit_chain.py` (32/32 passing).
  - **Demo script upgrade** — `scripts/demo_generate_soul.py` now builds a constitution for every generated soul, writes a sibling `<stem>.constitution.yaml`, binds the hash into the soul frontmatter, and records `agent_created` / `agent_spawned` events to `examples/audit_chain.jsonl`. The run ends with an audit chain verify() that must return ok=True with the expected entry count (11 = genesis + 5 role defaults + 2 stress + 3 lineage).
  - **Examples regenerated** under `examples/` — every soul now has a sibling `.constitution.yaml` plus frontmatter binding, and the checked-in `audit_chain.jsonl` shows the full demo event stream.
  - **ADR-0002 amendment** — `src/forest_soul_forge/soul/dna.py` moved to `src/forest_soul_forge/core/dna.py` (with all five call-site imports updated) so that `core/` no longer imports from `soul/`. `soul/` now depends on `core/` only, not the reverse. Amendment note appended to ADR-0002 explaining the relocation.

- Wave 1 polish (ADR-0002 — Agent DNA and lineage):
  - `src/forest_soul_forge/soul/dna.py` — deterministic SHA-256 hash of the canonical `TraitProfile` (role + sorted trait_values + sorted domain_weight_overrides). 12-char short DNA + 64-char full form. `verify()` helper accepts either form.
  - `Lineage` dataclass modeling the ancestor chain (root-first). Spawning agents use `Lineage.from_parent(parent_dna, parent_lineage, parent_agent_name)` to extend the chain; grandchildren preserve the full root-first ancestor list.
  - Every generated `soul.md` now opens with a YAML frontmatter block containing `dna`, `dna_full`, role, agent metadata, `parent_dna`, `spawned_by`, full `lineage` array, `lineage_depth`, every trait_value (sorted), and any domain_weight_overrides. The body becomes self-verifying: re-hash the frontmatter's trait block and compare to `dna`.
  - New prose format: each trait renders as `- **name** — value/100 (band). scale-text.` with an italicized description on the next line. `scale.mid` is now populated for all 26 traits, eliminating the earlier awkward `"low / high"` concat for moderate-band values.
  - Spawned agents (lineage depth > 0) emit a `## Lineage` footer listing the full root-first ancestor chain plus the agent's own DNA.
  - Docs: `docs/decisions/ADR-0002-agent-dna-and-lineage.md`, ADR index updated.
  - Examples regenerated under `examples/`, plus new `lineage_parent_huntmaster.soul.md`, `lineage_child_scout.soul.md`, `lineage_grandchild_forager.soul.md` demonstrating a 3-generation chain.

### Changed
- `Trait` dataclass gained `scale_mid` — required in `trait_tree.yaml` for v0.1, with a graceful fallback for legacy schemas that lack it.
- Soul prose rewritten to use bold trait names + banded values + italic descriptions. Drops the earlier robotic repeating-text format.
- `SoulGenerator.generate()` signature: new keyword-only `lineage: Lineage | None` parameter. Root agents default to `Lineage.root()`.

### Not yet started
- Agent factory (which will consume the `Lineage` primitives) and blue-team agents.
- Streamlit UI.
- LangGraph supervisor layer.
- Wave 2: SVG radar chart, profile diff tool, CLI (`generate`/`diff`/`list`/`validate`).
- Wave 3: expanded README, CONTRIBUTING.md, SECURITY.md, Makefile, `.editorconfig`, pre-commit, CI skeleton, golden-file snapshot tests.
