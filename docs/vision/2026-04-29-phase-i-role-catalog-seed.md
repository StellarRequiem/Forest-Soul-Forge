# Phase I role-catalog seed — 2026-04-29

Forest's role catalog is currently 14 (5 original + 9 swarm). Phase I
(catalog expansion) is queued to take that to ~30+. This doc is the
seed list — concrete role + skill ideas to pull from when Phase I
starts, so we don't begin from a blank page.

Source: a cross-check analysis from an external read of Forest in
late April 2026. Their "if it could make agents for any field"
section enumerated about a dozen specialization directions; we're
filing them here verbatim as the Phase I starting bench so the
expansion has external validation, not just our own taste.

---

## Specialization roles to forge

Each row sketches a role's domain, the genre it most plausibly fits
under, the trait emphasis we'd seed it with, and the tools/skills
it needs that don't yet exist. Treat this as **a menu**, not a
mandate — Phase I picks the most useful subset, not the whole list.

### Knowledge work

| Role | Genre | Trait emphasis | Needs (beyond current catalog) |
|---|---|---|---|
| `legal_researcher` | researcher | evidence_demand, thoroughness, citation_rigor (new trait?) | Local-doc reader; citation extractor; web_fetch on allowlist of legal-corpus hosts |
| `medical_literature_summarizer` | researcher | evidence_demand, hedging, double_checking | PubMed allowlist for web_fetch; explicit "NOT MEDICAL ADVICE" constitution clause |
| `financial_analyst` (local data only) | researcher | suspicion, double_checking, evidence_demand | Spreadsheet reader; stat libs; NO trade execution (operator-only) |
| `tutor` (per subject) | companion | empathy, patience, warmth | Per-subject knowledge base; pedagogical-pacing skills |
| `domain_researcher` (science / history / humanities) | researcher | curiosity, lateral_thinking, research_thoroughness | Subject-specific allowlists; bibliography skill |
| `creative_writing_collaborator` | companion | curiosity, formality (low), warmth | Voice-modeling; draft-revision skill chains |

### Multi-agent teams

The cross-check called these "agency in a box" — same shape as the
Security Swarm but generalized to non-defensive work. Each team is
a spawn pattern, not a single role.

| Team name | Composition | Pattern |
|---|---|---|
| **Research desk** | Researcher + Investigator + Communicator + Guardian | Researcher plans, Investigator drills into specific sources, Communicator synthesizes for human, Guardian challenges conclusions before sign-off |
| **Code review desk** | (existing) security_low + security_mid + new `code_reviewer` role | Static analysis → diff review → architectural pushback |
| **Writing desk** | Creative + Researcher + Editor (new role) | Draft → fact-check → critique → revise loop |
| **Triage desk** | Investigator + Communicator | Inbox / queue → categorize → draft response — operator approves |

Phase H (working agents loop) is the prerequisite — the run-loop
infrastructure these teams depend on lands there before any team
template ships in Phase I.

### Operator-side automation

| Role | Genre | Use |
|---|---|---|
| `home_automation_orchestrator` | actuator | Local home-assistant integration via mcp_call.v1; gated per device |
| `knowledge_base_curator` | observer | Watch a local note directory; index, deduplicate, surface stale items |
| `email_summarizer` | researcher | Local mailbox via IMAP allowlist; summary-only memory write per ADR-0027 |
| `download_organizer` | guardian | Watch ~/Downloads, classify, suggest moves — operator approves |

### Creative + exploratory

| Role | Genre | Use |
|---|---|---|
| `world_builder` | researcher | Long-form fictional-world consistency tracking; lineage memory |
| `game_design_helper` | researcher | Mechanics analysis, balance suggestions, playtest log review |
| `philosophical_companion` | companion | High evidence_demand + lateral_thinking; long-form dialogue partner |
| `simulation_agent` | researcher | High caution traits; runs scenario models with explicit assumption tracking |

### Security + privacy (extends ADR-0033 Security Swarm)

| Role | Genre | Use |
|---|---|---|
| `privacy_auditor` | observer | Scan local apps for telemetry endpoints; report what's phoning home |
| `deception_designer` | security_high | Honeypot + canary token configuration helper; extends DeceptionDuke |
| `vendor_risk_reviewer` | guardian | Read TOS / privacy policies (web_fetch); flag risky clauses |

---

## New traits the catalog might need

Several roles above suggest traits we don't have yet. Not all of
these need to land — some can be expressed via combinations of
existing traits — but the gaps are worth noting:

- `citation_rigor` — distinct from `evidence_demand`; specifically
  about source-attribution discipline. Could be a domain-specific
  modifier rather than a top-level trait.
- `pedagogical_pacing` — for the tutor role. Probably a skill
  pattern, not a trait.
- `narrative_continuity` — for the world-builder. Probably maps to
  high `thoroughness` + lineage memory.
- `aesthetic_sensitivity` — for creative collaborators. Hard to
  quantify; likely deferred.

The minimum viable Phase I doesn't add new traits — uses existing 29
in new combinations.

---

## What Phase I does NOT include (deliberate scope discipline)

The cross-check listed several directions Forest will **not** pursue
in Phase I:

- **Real-money agents.** Anything that holds bank credentials or
  initiates transactions stays operator-only. The per-agent secrets
  store (G2 shipped) is the substrate that *could* enable this
  someday, but Phase I doesn't unlock it.
- **Autonomous actions in regulated fields** (medical diagnosis,
  legal advice, investment recommendations). Forest can produce
  research and summaries; never advice. Constitution clauses
  enforce this per role.
- **Set-and-forget multi-agent swarms.** Even with Phase H's run
  loop, every state-changing action stays gated by approval. The
  cross-check's "real-time oversight scales poorly with many
  agents" is a fair concern; the answer is per-tier approval
  graduation (existing) + per-genre risk floor (existing) + sane
  default trait values that bias toward operator-asks-first.

---

## Sequencing for Phase I

Once Phase H ships the working agents loop, Phase I rolls out roles
in this order — easiest-to-most-novel:

1. **Research desk** (4 roles + spawn template). Composes existing
   primitives; needs only Phase H + Phase G (open-web).
2. **Knowledge base curator + download organizer** (operator-side
   automation). Local-only; lowest risk surface.
3. **Code review desk** (extends existing security tier). Validates
   the multi-agent template against a familiar domain.
4. **Tutor + creative collaborator** (companion-tier work). Requires
   companion-tier hardening (pillar 2) — gates on F-track + H.
5. **Domain researchers** (science / history / etc). Each one is a
   YAML edit + small skill bundle once the general pattern works.
6. **Specialized professional roles** (legal, medical, financial).
   Highest legal/regulatory care; ships with explicit constitution
   clauses + caution defaults.

Every role lands as: trait_tree.yaml entry + constitution_templates.yaml
role_base + tool_catalog.yaml archetype kit + 2-5 skill manifests in
examples/skills/. Each step takes ~half day once the template is
solidified.

---

## Provenance

Cross-check from an isolated Claude session reading Forest's public
docs in late April 2026. The expansion list reflects an external
reader's view of where Forest's design naturally extends — useful as
external validation that the Phase I shape is recognizable, not just
internally coherent.
