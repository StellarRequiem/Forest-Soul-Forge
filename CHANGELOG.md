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
- Phase 2 remainder: grading engine, constitution builder, audit chain.
- Agent factory (which will consume the `Lineage` primitives) and blue-team agents.
- Streamlit UI.
- LangGraph supervisor layer.
- Wave 2: SVG radar chart, profile diff tool, CLI (`generate`/`diff`/`list`/`validate`).
- Wave 3: expanded README, CONTRIBUTING.md, SECURITY.md, Makefile, `.editorconfig`, pre-commit, CI skeleton, golden-file snapshot tests.
