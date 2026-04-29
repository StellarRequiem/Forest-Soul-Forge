# 2026-04-30 — Load-bearing survey of `src/forest_soul_forge/`

Filed as Tier 1 of the post-marathon roadmap. Classifies every Python
module by load category before the conversation runtime (ADR-003Y) lands
on top of today's stack.

## Methodology

- **Granularity:** file-level (per the agreed scope decision).
- **Format:** classification table + narrative annotations (per agreed
  format).
- **Source data:** `find` + `wc -l` for size, `git log` for recency,
  `grep -nE "^class|^def"` outlines for the 4 largest files, full reads
  for the god-object candidates, terse rows for everything else.
- **Total scope:** 116 Python files across 11 packages, ~31,400 LoC.

## Classification taxonomy

| Tag                       | Meaning                                                                                          |
|---------------------------|--------------------------------------------------------------------------------------------------|
| `LOAD-BEARING-NOW`        | Currently in the hot path of shipped features. Breakage = visible failure.                      |
| `LOAD-BEARING-SOON`       | Not currently hot, but ADR-003Y (conversation runtime) will route through it.                    |
| `LOAD-BEARING-LATER`      | On a queued ADR's path (Tier 4/5 items in the roadmap).                                          |
| `SCAFFOLDED-UNUSED`       | Imported by something but no agent / no call path actually exercises it under real workload.    |
| `DECORATIVE`              | Maintained because it ships, not because anyone runs it. Tests + manifests in this category.    |
| `RECENTLY-TOUCHED`        | Modified in the last 24 hours. Marker, not a category — flags where today's marathon landed.    |

---

## TL;DR — the 5 things to change

1. **Split `daemon/routers/writes.py` (1,215 LoC)** into a thin router + a
   `daemon/birth_pipeline.py` module. The 4 endpoints (`/birth`, `/spawn`,
   `/regenerate-voice`, `/archive`) are reasonable to co-locate, but the
   ~20 helper functions doing constitution build / kit resolution / voice
   render / artifact rollback are pipeline steps, not router code. Today's
   K6 addition (hardware_binding) lives in BOTH /birth and /spawn paths
   as duplicated code — extracting the pipeline collapses that
   duplication.

2. **Split `daemon/schemas.py` (1,139 LoC)** into a `daemon/schemas/`
   package with one file per domain (`agents.py`, `audit.py`,
   `hardware.py`, `triune.py`, `traits.py`, `tools.py`, `character.py`,
   `health.py`, `genres.py`). 40+ Pydantic classes in one file makes
   navigation hostile. No logic to extract — pure organizational refactor.
   Should ship before ADR-003Y adds another 5-10 schemas for conversation.

3. **Extract `governance_pipeline` from `tools/dispatcher.py` (1,108 LoC)**.
   `ToolDispatcher.dispatch()` has 7+ inline pre-execute checks: lookup,
   validate, constraints, counter, K6 quarantine, K4 (in delegator), genre
   floor, approval gate. Refactor: `governance_pipeline.run(call_ctx) →
   PipelineResult` returns either GO, REFUSE, or PENDING_APPROVAL.
   `dispatch()` becomes a clear orchestrator over named pipeline steps.
   **This refactor de-risks ADR-003Y.** Conversation runtime will need to
   add at least 1 more pre-execute check (per-conversation rate limit) and
   the ambient nudge path is going to want some of these checks but not
   others. A pipeline you can compose is better than another `if/elif/elif`
   block in `dispatch()`.

4. **Split `registry/registry.py` (1,192 LoC)** into per-table accessor
   classes — `AgentRegistry`, `SecretsRegistry`, `MemoryConsentRegistry`,
   `AuditEventRegistry`, `ToolCallRegistry` — with a thin `Registry`
   façade for backward compatibility. Every K-track addition stuck more
   methods on the single Registry class (set_secret, mark_verified,
   list_secret_names, ...). The class will only get bigger when ADR-003Y
   adds Conversation/Turn methods. Façade pattern means callers don't
   change while the internals split apart.

5. **File `daemon/routers/hardware.py` (98 LoC) is the smallest existing
   K-track router.** Use it as the template for ADR-003Y's
   `routers/conversations.py` so the new code matches the K-track pattern
   instead of inheriting the writes.py shape.

These are real refactor recommendations, not nice-to-haves. They're sized:
#1 + #2 + #3 are each ~1 day; #4 is ~2 days; #5 is "use this template,
not that one" — zero code, just orientation.

---

## God-object candidates (deep dive)

### `daemon/routers/writes.py` — 1,215 LoC — LOAD-BEARING-NOW + RECENTLY-TOUCHED

| Concern                              | LoC range  | Notes                                                                                       |
|--------------------------------------|------------|---------------------------------------------------------------------------------------------|
| Endpoint definitions (4)             | 501-1215   | `/birth` (224 lines), `/spawn` (274), `/regenerate-voice` (~155), `/archive` (~60)         |
| Helper functions (~20)               | 116-500    | Profile build, kit resolution, voice render, artifact write/rollback, idempotency           |
| Hardware-binding (K6 today)          | 618-674 in BOTH /birth AND /spawn | DUPLICATED — extract via pipeline                                                |

**Smell:** /birth and /spawn are 80% the same code with the spawn-specific
parent-lineage step folded in. Today's K6 addition got pasted into both.
Tomorrow's ADR-003Y will need the same — every birth-time policy lands
in two places. Pipeline extraction stops the duplication.

**Recommendation:** Extract `daemon/birth_pipeline.py`:

```python
class BirthPipeline:
    def __init__(self, registry, audit, tool_catalog, genre_engine, settings, providers): ...

    def run(self, request: BirthRequest, parent: AgentRow | None = None) -> BirthResult:
        profile = self._build_profile(request.profile)
        kit = self._resolve_kit(profile, request.tools_add, request.tools_remove)
        constitution = self._build_constitution(profile, kit, request)
        binding = self._maybe_bind_hardware(request)  # ← K6 lives here, ONCE
        soul = self._render_soul(constitution, request.enrich_narrative)
        return BirthResult(profile, kit, constitution, soul, binding)
```

Routers become ~50 LoC each (validate request → call pipeline → emit
event → register row → return AgentOut).

### `tools/dispatcher.py` — 1,108 LoC — LOAD-BEARING-NOW + LOAD-BEARING-SOON + RECENTLY-TOUCHED

| Concern                              | LoC range  | Notes                                                                                       |
|--------------------------------------|------------|---------------------------------------------------------------------------------------------|
| Result dataclasses (4)               | 81-150     | DispatchSucceeded/Refused/PendingApproval/Failed                                            |
| Pre-execute checks                   | 287-415    | Inline in dispatch(): lookup → validate → constraints → counter → K6 quarantine → genre floor → approval gate |
| Execute + audit emit                 | 415-720    | tool.execute() + per-status audit event paths + per-call accounting                         |
| Approval-resume path                 | 720-970    | Resume a pending call after operator approves                                               |
| Helper functions                     | 970-1108   | _digest, _provider_is_local, _check_genre_floor, _canonical_json, K6 _hardware_quarantine_reason |

**Smell:** `dispatch()` is the routing hot path AND the policy hot path.
Every K-track item I added today (K6 quarantine) made this longer. ADR-003Y
will need: per-conversation rate-limit check, ambient-quota check, and
possibly a "is this turn from operator or another agent" branch. The
inline if/else chain doesn't compose.

**Recommendation:** Pipeline extraction (item #3 in TL;DR). Specifically:

```python
class GovernancePipeline:
    """Pre-execute checks. Returns GO / REFUSE / PENDING_APPROVAL."""

    steps: list[PipelineStep]   # ordered; each step.evaluate(ctx) → StepResult

    def run(self, ctx: DispatchContext) -> PipelineResult:
        for step in self.steps:
            result = step.evaluate(ctx)
            if result.terminal:   # REFUSE or PENDING_APPROVAL
                return result
        return PipelineResult(verdict="GO")
```

Steps become single-purpose classes:
`ToolLookupStep`, `ArgsValidationStep`, `ConstraintResolutionStep`,
`CallCounterStep`, `HardwareQuarantineStep` (K6),
`GenreFloorStep`, `ApprovalGateStep`.

`dispatch()` becomes:

```python
async def dispatch(self, ...):
    ctx = self._build_context(...)
    verdict = self.governance_pipeline.run(ctx)
    if verdict.refused:   return self._emit_refused(...)
    if verdict.pending:   return self._emit_pending(...)
    return await self._execute_and_account(ctx)
```

Adds a real `tools/governance_pipeline.py` (~150 LoC), shrinks `dispatch()`
to ~80 LoC, makes ADR-003Y additive (just register a new step in the
pipeline list).

### `registry/registry.py` — 1,192 LoC — LOAD-BEARING-NOW

| Concern                           | LoC range  | Notes                                                                                |
|-----------------------------------|------------|--------------------------------------------------------------------------------------|
| Error classes                     | 36-72      | RegistryError + 4 subclasses                                                          |
| Row dataclasses                   | 74-115     | AgentRow, AuditRow, RebuildReport                                                     |
| `Registry` class methods          | 118-1088   | 970 lines. Bootstrap, agent CRUD, audit ingest, idempotency cache, tool-call writer, approval queue, secrets, memory consents, memory verifications |
| Module helpers                    | 1088-1186  | Transaction context manager, _row_to_* converters                                     |

**Smell:** Every K-track addition stacked methods onto Registry. K1 added
mark_verified/unmark_verified/is_verified/get_verifier. K6 didn't add
methods (it lived in dispatcher), but ADR-003Y will add:
create_conversation, list_conversations, get_conversation,
add_participant, append_turn, summarize_window, etc. Without splitting,
Registry will cross 1,500 LoC.

**Recommendation:** Façade pattern — split, then have Registry expose the
old methods via delegation:

```python
src/forest_soul_forge/registry/
├── __init__.py          # re-exports Registry façade
├── _agents.py           # AgentRegistry — agents + ancestry + sibling_index
├── _audit.py            # AuditEventRegistry — audit_events + tool_calls
├── _approvals.py        # ApprovalRegistry — tool_call_pending_approvals
├── _secrets.py          # SecretsRegistry — agent_secrets (G2)
├── _consents.py         # MemoryConsentRegistry — memory_consents + memory_verifications (K1)
├── _conversations.py    # ConversationRegistry — conversations + turns + participants (Y)
├── schema.py            # (existing) — schema migrations stay
└── registry.py          # Façade — instantiates the per-table classes,
                         #          re-exposes their methods, preserves
                         #          backward-compat for callers
```

Each per-table file is ~200 LoC. The façade is ~80 LoC of delegation.
Callers don't change. The next ADR's additions go into the right
sub-file, not the megaclass.

### `daemon/schemas.py` — 1,139 LoC — LOAD-BEARING-NOW + RECENTLY-TOUCHED

| Concern                  | LoC range  | Notes                                                          |
|--------------------------|------------|----------------------------------------------------------------|
| Agent CRUD models        | 19-220     | AgentOut, AgentListOut, BirthRequest, SpawnRequest, ArchiveRequest |
| Hardware (K6 today)      | 167-178    | HardwareUnbindRequest/Response                                 |
| Audit + ceremony (K2)    | 224-265    | AuditEventOut, AuditListOut, CeremonyEmitRequest/Response      |
| Triune (K4)              | 269-285    | TriuneBondRequest/Response                                     |
| Health                   | 287-330    | ProviderHealthOut, StartupDiagnostic, HealthOut                |
| Provider                 | 335-395    | ProviderInfoOut, SetProviderIn, GenerateRequest/Response       |
| Trait tree (read)        | 399-470    | TraitOut, SubdomainOut, DomainOut, RoleOut, FlaggedCombinationOut, TraitTreeOut |
| Tool catalog (read)      | 472-545    | ToolDefOut, ArchetypeBundleOut, ToolCatalogOut, RegisteredToolOut, RegisteredToolsOut |
| Resolved kit             | 547-578    | ResolvedToolOut, ResolvedKitOut                                |
| Genre                    | 580-625    | GenreRiskProfileOut, GenreOut, GenresOut                       |
| Character sheet          | 627-1139   | ~510 LoC of nested character-sheet models                      |

**Smell:** Not a god-object in the algorithmic sense (no logic, no methods
beyond Pydantic field declarations). But navigating it is hostile —
related models are scattered (BirthRequest at line 82, agent ancestry
helpers at line 627). Today's K-track additions landed in three different
spots in the file.

**Recommendation:** Pure organizational refactor. Split into `daemon/schemas/`
package, one file per domain. No logic moves; just imports redirect via
`__init__.py` re-exports for backward compatibility. ~1 day; should ship
**before** ADR-003Y adds 5-10 more conversation/turn schemas.

---

## Medium-tier files (500-700 LoC) — single-purpose, healthy

| File                                             | LoC | Classification                                  | Notes                                                |
|--------------------------------------------------|-----|------------------------------------------------|------------------------------------------------------|
| `forge/tool_forge.py`                            | 661 | LOAD-BEARING-NOW                                | 6-stage forge pipeline; tightly scoped               |
| `core/memory.py`                                 | 637 | LOAD-BEARING-NOW + RECENTLY-TOUCHED + LOAD-BEARING-SOON | K1 added verification methods; Y will write turn bodies here |
| `forge/skill_expression.py`                      | 601 | LOAD-BEARING-NOW                                | Has the compile_arg dispatch (T0.2 fix lives here)   |
| `forge/skill_runtime.py`                         | 599 | LOAD-BEARING-NOW                                | Skill execution engine                               |
| `registry/schema.py`                             | 560 | LOAD-BEARING-NOW + RECENTLY-TOUCHED + LOAD-BEARING-SOON | v9 ships K1; v10 will ship Y conversations          |
| `soul/voice_renderer.py`                         | 547 | LOAD-BEARING-NOW                                | LLM-backed voice section; templated fallback         |
| `core/audit_chain.py`                            | 541 | LOAD-BEARING-NOW + RECENTLY-TOUCHED + LOAD-BEARING-SOON | tail() helper from earlier; Y emits more events     |
| `core/constitution.py`                           | 516 | LOAD-BEARING-NOW                                | constitution_hash logic; canonical_body() carefully excludes K4/K6 additive blocks |
| `forge/static_analysis.py`                       | 491 | LOAD-BEARING-NOW                                | Tool Forge static checks                             |
| `core/genre_engine.py`                           | 483 | LOAD-BEARING-NOW                                | Genre runtime + role-to-genre mapping               |
| `cli/install.py`                                 | 480 | LOAD-BEARING-NOW                                | Skill install + tool plugin install                  |
| `chronicle/render.py`                            | 473 | RECENTLY-TOUCHED + LOAD-BEARING-SOON            | K5 NEW today; will be the surface for Y conversation export |
| `soul/generator.py`                              | 462 | LOAD-BEARING-NOW                                | soul.md renderer                                     |
| `daemon/app.py`                                  | 450 | LOAD-BEARING-NOW + RECENTLY-TOUCHED             | Lifespan + router registration; K-track added 3 routers today |

None of these are smells. All are within healthy size for what they do.
Only `chronicle/render.py` deserves a flag for ADR-003Y — its sanitizer
table will need conversation event types.

---

## Per-package summary (everything else)

### `core/` — 11 files, ~3,000 LoC
**LOAD-BEARING-NOW** for almost everything. Trait engine, constitution,
audit chain, memory, genre engine, secrets, hardware fingerprint, tool
catalog, tool policy, dna, grading. The one outlier:

- `core/secrets.py` (280 LoC) — RECENTLY-TOUCHED + LOAD-BEARING-NOW.
  G2 ships it; web_fetch + browser_action + mcp_call all reference it for
  per-agent auth. `MasterKey` is generated once and held in process memory.

### `daemon/` — 27 files (incl. routers/), ~7,500 LoC
- `app.py` + `config.py` + `deps.py` are the lifespan + dependency
  plumbing. Healthy.
- `routers/` has 17 router files. The 3 god-objects (writes, schemas,
  dispatcher) are NOT in `routers/` — schemas + dispatcher are top-level,
  writes IS in routers but isn't a router-shape problem.
- The K-track routers I added today (`triune.py`, `hardware.py`,
  `audit.py` modifications) follow a clean pattern: ~100-300 LoC each,
  one or two endpoints, write_lock + audit emit + return. **Use these
  as the template for Y.**
- `routers/character_sheet.py` (396) is the longest of the routers but
  appropriate for the 510-LoC character schema in schemas.py — they're
  twin god-data-models, not god-logic.

### `tools/` — 35 files (incl. builtin/), ~9,500 LoC
- `dispatcher.py` (1108) is the god-object — see deep-dive above.
- `delegator.py` (340) — RECENTLY-TOUCHED. K4 enforcement logic. Healthy.
- `base.py` (340) — RECENTLY-TOUCHED. Added agent_registry today (G6).
  Could absorb the future Pipeline result types when item #3 ships.
- `tools/builtin/*.py` — 30 files, each ~200-400 LoC. Each is a single
  tool implementation. Self-contained. The 3 newest (web_fetch,
  browser_action, mcp_call) are the largest at ~330 LoC; older security-
  swarm tools average ~250 LoC. Healthy.
- `plugin_loader.py` (297) — operator-installed `.fsf` plugins. Used by
  Tool Forge install path. Healthy.

### `registry/` — 4 files, ~2,200 LoC
- `registry.py` (1192) god-object — see deep-dive.
- `schema.py` (560) — schema versioning + migrations. v9 ships today.
- `ingest.py` (365) — chain-to-registry replay. LOAD-BEARING-NOW for
  /audit/agent endpoints.
- `__init__.py` — re-exports. Trivial.

### `forge/` — 6 files, ~2,400 LoC
- `tool_forge.py` + `skill_runtime.py` + `skill_expression.py` + `skill_manifest.py`
  + `static_analysis.py` — see medium-tier table.
- All healthy. The forge subsystem is well-factored already (separate
  manifest parser, expression compiler, runtime, forge orchestrator).

### `soul/` — 3 files, ~1,000 LoC
- `voice_renderer.py` (547) — LLM voice section.
- `generator.py` (462) — soul.md renderer.
- `__init__.py` — re-exports.
- All healthy.

### `cli/` — 6 files, ~1,200 LoC
- `main.py` (312) + `install.py` (480) + `triune.py` (155) +
  `chronicle.py` (185) + `forge_tool.py` (~?) + `forge_skill.py` (~?) +
  `_common.py` (62).
- `main.py` will keep growing as new subcommands ship (Y will likely add
  `fsf chat`). Watch for it crossing 500 LoC; not urgent.

### `chronicle/` — 2 files, ~480 LoC (NEW today, K5)
- `render.py` (473) — see medium-tier.
- `__init__.py` — re-exports.

### `agents/` — 1 file, ~?
- Single empty placeholder. Not used.
- **CLASSIFICATION:** SCAFFOLDED-UNUSED. Could be deleted; nothing
  imports it.

### `security/` — 2 files, ~?
- ADR-0033 PrivClient subprocess wrapper.
- LOAD-BEARING-NOW when `FSF_ENABLE_PRIV_CLIENT=true`. Otherwise
  privileged tools refuse cleanly with "no PrivClient wired."
- Healthy.

### `ui/` — 4 files, ~?
- Vanilla JS frontend. Outside the Python LoC count above.
- Will get a Chat tab from Y6. Worth a separate audit when that lands.

---

## RECENTLY-TOUCHED summary (today's marathon footprint)

20 Python files modified in the last 24h. Concentration:

- **`tools/dispatcher.py`** (+34 lines for K6 quarantine + agent_registry)
- **`daemon/routers/writes.py`** (+50 lines for K6 hardware_binding x2 paths)
- **`daemon/schemas.py`** (+25 lines for HardwareUnbindRequest/Response, TriuneBond models)
- **`daemon/app.py`** (+4 lines for new router registrations)
- **`daemon/deps.py`** (+5 lines for agent_registry plumbing)
- **`tools/base.py`** (+12 lines for agent_registry field)
- **`tools/delegator.py`** (+85 lines for K4 enforcement)
- **`core/audit_chain.py`** (+50 lines for tail/SSE)
- **`core/memory.py`** (+60 lines for K1 verification methods)
- **`core/secrets.py`** (NEW — G2)
- **`core/hardware.py`** (NEW — K6)
- **`chronicle/render.py`** (NEW — K5)
- **`tools/builtin/{web_fetch,browser_action,mcp_call,suggest_agent,memory_verify}.py`** (NEW — G3-G6, K1)
- **`daemon/routers/{audit,triune,hardware}.py`** (audit MODIFIED for K2/K3; triune NEW K4; hardware NEW K6)
- **`cli/{triune,chronicle}.py`** (NEW — K4, K5)

The marathon stayed in expected places. No surprise hot-spots. The 3
god-objects all received additions today, which validates the refactor
recommendations above (they're getting hit, not just sitting).

---

## What's NOT load-bearing

Some genuine SCAFFOLDED-UNUSED finds:

- **`agents/__init__.py`** — empty placeholder, no imports. **DELETE.**
- **`security/__init__.py`** — empty if FSF_ENABLE_PRIV_CLIENT=false (which it almost always is on dev machines). **KEEP** (it's the API surface), but flag.
- **Aspirational genres** in `config/genres.yaml`: `actuator`, `guardian`, `researcher` claim no roles in `trait_tree.yaml`. The genre exists but no agent can be born into it. This is by design (placeholder for future expansion) but worth knowing about — when Phase I ships role types, these become live.
- **3 web genres added today** (`web_observer`, `web_researcher`, `web_actuator`) — same status. They claim `web_observer_root` / etc. role names that don't exist in trait_tree.yaml yet. Reserved for G6b. Will become live when role types land.

---

## Recommended sequence (folding back into the roadmap)

The 5 refactor recommendations above slot into the existing roadmap as:

| Refactor                                         | Slot                                          | Effort |
|--------------------------------------------------|-----------------------------------------------|--------|
| #2 schemas.py split                              | Right before Y1 (so Y schemas land in the new structure) | 1 day |
| #1 writes.py → birth_pipeline.py                 | Right before Y1 (so Y can reuse pipeline patterns) | 1 day |
| #3 dispatcher.py → governance_pipeline.py        | Right before Y3 (Y3 needs to add a per-conversation rate-limit check) | 1 day |
| #4 registry.py → per-table classes               | Right before Y1 (Y needs a `_conversations.py`) | 2 days |
| #5 use hardware.py as conversations.py template  | At the start of Y1                            | 0 days |

So the practical sequence is:

```
After Tier 0 (done):
  T1.1 audit (this doc)              ← DONE
  T2.1 governance-relaxed event      (parallel with this audit)

Before Y1:
  R1: schemas.py split               (1 day)
  R2: writes.py → birth_pipeline     (1 day)
  R4: registry.py → per-table        (2 days)

Y1 → Y2:
  ... (clean foundation; ADR-003Y schemas land in the new structure)

Before Y3:
  R3: dispatcher.py → governance_pipeline   (1 day)
```

Total refactor cost: ~5 days, distributed across the natural pause points
in the conversation runtime work. None of them are pure interest — each
one demonstrably makes the next ADR-003Y phase easier.

---

## Confidence + caveats

- **Confidence high on:** the 4 god-object identifications, their LoC
  numbers, the touch-frequency mapping, the recommended split shapes.
- **Confidence medium on:** the exact effort estimates (1 day, 2 days).
  Real-world refactor work routinely takes 1.5-2x estimate when test
  coverage gaps surface.
- **Caveat 1:** Refactor #4 (per-table registry façade) requires reading
  every Registry method to verify the categorization. There may be
  cross-cutting methods that don't slot cleanly into one table file —
  those become façade-level utilities. Worth a half-day spike before
  committing the full 2 days.
- **Caveat 2:** The frontend (`ui/`) was not surveyed as part of this
  audit. Y6 will add a Chat tab; that work should include a parallel
  frontend audit for similar god-script smells in the existing JS.
- **Caveat 3:** Test coverage was NOT surveyed. Several modules have
  inline tests in adjacent comments (mentioned in commits) but no
  pytest-discoverable file. A coverage audit is a separate piece of work
  if/when CI becomes a priority.

## Closing read

The codebase is in much better shape than I expected after 18 commits in
a single session. The god-object problem is real but localized to 4 files,
and each has a clean refactor path. The K-track additions stayed in their
lanes — no new cross-cutting smell emerged. The chronicle and triune
work added 2 NEW packages (chronicle/, examples/constitutions/triune/)
without bloating existing ones.

The conversation runtime (ADR-003Y) is the largest commitment ahead, and
the 5 refactors above are the prerequisite work that makes it shippable
without compounding the god-object problem. They should be folded into
the roadmap before Y1 starts.
