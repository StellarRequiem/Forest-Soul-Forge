# 2026-05-13 — Three-ADR Arc (Sessions B248-B258)

Session-end audit documenting the architectural changes
landed between commit `5d3662e` (post-B247 baseline) and
`cd83e83` (post-B258 head). Three load-bearing ADRs closed
end-to-end, plus a critical hotfix surfaced by the
verification-discipline test run.

## TL;DR

| ADR | Title | Status before | Status after | Bursts |
|---|---|---|---|---|
| ADR-0061 | Agent Passport for cross-machine roaming | T1-T5 shipped (B246-B247) | **Closed** — T6 HTTP endpoint + T7 CLI subcommand + audit events | B248 |
| ADR-0062 | Supply-Chain IoC Scanner + Install Gate | (new, drafted) | **Closed** — 6/6 tranches across catalog + builtin + install gate + forge-stage scanner + SoulUX Security tab | B249, B250, B257, B258 |
| ADR-0063 | Reality Anchor (persistent ground-truth verification) | (new, drafted) | **Closed** — 7/7 tranches across substrate + dispatcher gate + agent role + conversation hook + correction memory + SoulUX Reality tab | B251-B256 |

12 commits total (8 features + 1 hotfix + 1 test-fixture fix
+ 2 ADR closures). Schema bumped v19 → v20 (ADR-0063 T6
correction memory table). Frontend gained 2 new operator-
facing tabs (Reality, Security).

## What's new vs. the pre-session baseline

### Cryptographic substrate (ADR-0061)

The B246-B247 work shipped the passport primitives + K6
quarantine integration. B248 closed the ADR by adding:

- **HTTP endpoint** — `POST /agents/{instance_id}/passport`
  mints a passport authorizing the agent to run on a set of
  hardware fingerprints. Body: `authorized_fingerprints`
  (required, min 1), optional `expires_at` + `operator_id`
  + `reason`. Resolves operator master via
  `resolve_operator_keypair`, agent public_key from registry,
  mints + writes `passport.json` next to constitution under
  the write lock, emits `agent_passport_minted` audit event.
  404 on unknown agent, 409 if agent lacks `public_key`
  (legacy pre-ADR-0049 agent), 422 on empty fingerprint list.

- **CLI subcommand** — `fsf passport {mint, show, fingerprint}`
  via `cli/passport_cmd.py`. `mint` posts to the HTTP
  endpoint. `show` reads `passport.json` off disk (no HTTP).
  `fingerprint` prints the local machine's hardware
  fingerprint (script-friendly: fp on stdout, source on
  stderr).

- **Audit events** — `agent_passport_minted` (router emits on
  success) and `agent_passport_refused` (HardwareQuarantineStep
  emits when quarantine descriptor surfaces a passport_reason)
  added to `KNOWN_EVENT_TYPES`.

### Supply-chain defense (ADR-0062)

New ADR. Six tranches closed across four bursts. Direct
response to the 2025-26 npm Shai-Hulud worm generations
(Sep 2025 → Nov 2025 → Feb 2026), LiteLLM/Telnyx PyPI
compromise (April 2026), Axios npm compromise (April 2026,
NK-nexus UNC1069), and the Anthropic MCP STDIO RCE
disclosure (April 2026, ~200K vulnerable instances).

The defense surface now spans every artifact lifecycle
stage:

| Stage | Gate | When |
|---|---|---|
| **Forge propose** (T5, B257) | `scan_forge_stage_or_refuse` | LLM emits artifact → CRITICAL findings refuse + write `REJECTED.md` |
| **Forge install** (T5, B257) | `staged_dir_is_quarantined` | Operator clicks install on quarantined dir → 409 |
| **Marketplace install** (T4, B250) | `scan_install_or_refuse` | Third-party plugin contains IoC → 409 |
| **Skill/Tool install** (T4, B250) | `scan_install_or_refuse` | Manual install of malicious staged artifact → 409 |
| **Operator UI** (T6, B258) | `/security/*` router + Security tab | Operator sees IoC catalog + recent scans + quarantined proposals |

T1 added `config/security_iocs.yaml` — a 16-rule IoC
pattern catalog covering Shai-Hulud, MCP-STDIO-RCE,
LiteLLM/Telnyx, Axios, and typosquat patterns. Each rule
carries severity (CRITICAL/HIGH/MEDIUM/LOW/INFO) +
domain_keywords + canonical_terms + forbidden_terms +
incident URL.

T2 added `security_scan.v1` builtin tool. T3 wired the
scanner into the governance pipeline. T4 wired install-time
refusal into three install endpoints. T5 wired pre-stage
refusal into the two forge endpoints + added a structural
`REJECTED.md` marker that the install endpoints honor.
T6 added the SoulUX Security tab.

### Reality Anchor (ADR-0063)

New ADR. Seven tranches closed across six bursts. The
operator-facing differentiator the ELv2 business model
needs: "Forest agents run with a Reality Anchor — your
agent can't silently drift past your facts."

Substrate spans three integration surfaces:

| Surface | Gate | When |
|---|---|---|
| **Dispatcher** (T3, B252) | `RealityAnchorStep` in governance pipeline | Every gated tool call cross-checked against ground truth before execution |
| **Conversation** (T5, B254) | `check_turn_against_anchor` | Every assistant turn cross-checked before it lands in the conversation log |
| **Agent layer** (T4, B253) | `reality_anchor` role | Singleton-per-forest agent other agents delegate to for deep LLM-grade verification |
| **Operator UI** (T7, B256) | `/reality-anchor/*` router + Reality tab | Operator sees ground truth + recent flags + repeat offenders |

T1 added `config/ground_truth.yaml` — 14 bootstrap operator-
asserted facts (operator identity, license, repo URL,
daemon URL, platform, python version, schema version,
audit chain canonical path, write_lock pattern, plus two
CRITICAL invariants for DNA + constitution hash
immutability). Each fact has the same shape as the IoC
rules (domain_keywords + canonical_terms + forbidden_terms
+ severity).

T2 added `verify_claim.v1` builtin tool. T3 wired the
substrate-layer gate into the dispatcher pipeline.

T4 added the `reality_anchor` role across all four catalog
files (trait_tree, genres, tool_catalog, constitution_templates)
+ structural singleton enforcement at `/birth` (second
spawn returns 409 with the existing instance_id).

T5 wired the conversation runtime pre-turn hook with
distinct event-type pair (`reality_anchor_turn_refused` /
`reality_anchor_turn_flagged`) so audit queries separate
turn-refused from tool-call-refused.

T6 added the correction memory: schema v20
`reality_anchor_corrections` table with PRIMARY KEY on
sha256 of normalized claim text. Both surfaces bump
`repetition_count` on every contradicted finding; when
post-bump count > 1, `reality_anchor_repeat_offender` fires.
Worst-severity escalates only (LOW → HIGH overwrites;
HIGH → LOW preserves HIGH).

T7 added the SoulUX Reality tab + `/reality-anchor/*`
operator-facing router with five endpoints.

## Verification arc + uncovered bugs

Post-session pytest run found two bug classes:

**Production hotfix (B256.1, commit `c24f63d`):**
B253 added `reality_anchor` to `trait_tree.yaml` with
`emotional: 0.3` + `embodiment: 0.3` — both below the
validator's `[0.4, 3.0]` range. The trait engine silently
failed to load at lifespan; every `/birth` returned 503.
Diagnosed via standalone TraitEngine() driver after the
pytest run surfaced 11 503-failing tests. Fix: clamp both
weights to 0.4 (the floor). Semantic intent preserved.
Inline comment added pointing at the validator constraint.

**Test-fixture fixes (B256.2, commit `d588db7`):**
Three tests had real bugs unrelated to the substrate:
- `_staged_*` helpers in test_install_scanner used
  `mkdir()` without `parents=True`, breaking when called
  with a non-existent parent path.
- `test_archive_then_rebirth_succeeds` posted to
  `/agents/archive` (wrong URL). Actual endpoint is
  `POST /archive`.

After the two hotfixes, the session test pass rate went
from 96% (132/138) → 100% (147/147).

The takeaway documented in B256.1's commit message: per-burst
standalone smoke drivers are insufficient. Full pytest suite
catches cross-cutting validation failures that the
single-purpose smoke driver pattern misses. The CLAUDE.md
"After every batch of changes: run the full suite" discipline
is load-bearing for a reason.

## Schema delta

| Version | Burst | Change |
|---|---|---|
| v19 (pre-session) | B243 | `agents.public_key` column (ADR-0049 T4) |
| **v20** | **B255** | `reality_anchor_corrections` table (ADR-0063 T6) |

Migration path: 19 → 20 adds the table + 3 indexes (on
`contradicts_fact_id`, `last_agent_dna`, `repetition_count DESC`).
Pure addition. `REBUILD_TRUNCATE_ORDER` updated to clear
the new table on rebuild-from-artifacts.

## Audit event delta

7 new event types added to `KNOWN_EVENT_TYPES`:

| Event type | Emitter | Triggered when |
|---|---|---|
| `agent_passport_minted` | passport router (B248) | Operator successfully mints a passport |
| `agent_passport_refused` | HardwareQuarantineStep (B248) | K6 quarantine consulted passport, rejected |
| `agent_security_scan_completed` | install_scanner / forge_stage_scanner (B250+B257) | Any install-time or forge-stage scan completes (allow + refuse paths both emit) |
| `reality_anchor_refused` | RealityAnchorStep (B252) | Dispatcher gate refused on CRITICAL ground-truth contradiction |
| `reality_anchor_flagged` | RealityAnchorStep (B252) | Dispatcher gate flagged HIGH/MEDIUM/LOW |
| `reality_anchor_turn_refused` | check_turn_against_anchor (B254) | Conversation gate refused a turn |
| `reality_anchor_turn_flagged` | check_turn_against_anchor (B254) | Conversation gate flagged a turn |
| `reality_anchor_repeat_offender` | both T3 + T5 surfaces (B255) | Same hallucinated claim seen 2+ times |

## Frontend delta

Two new SoulUX tabs:

- **Reality** (`data-tab="reality-anchor"`) — ADR-0063 T7.
  4 panel sections: status card, ground-truth facts table,
  recent events timeline, repeat offenders. Lazy-loads on
  first tab click.
- **Security** (`data-tab="security"`) — ADR-0062 T6. Same
  4-section shape: status card, IoC catalog table, recent
  scans timeline, quarantined proposals. Reuses severity-
  chip + table styles from the Reality tab.

Both panes are read-only by design. Operator edits the
underlying YAML (`config/ground_truth.yaml` /
`config/security_iocs.yaml`) directly and clicks the
Reload button to pick up changes without a daemon restart.

## Files added or significantly extended

### Production code

- `src/forest_soul_forge/daemon/install_scanner.py` (NEW, B250)
- `src/forest_soul_forge/daemon/forge_stage_scanner.py` (NEW, B257)
- `src/forest_soul_forge/daemon/reality_anchor_turn.py` (NEW, B254)
- `src/forest_soul_forge/daemon/routers/passport.py` (NEW, B248)
- `src/forest_soul_forge/daemon/routers/reality_anchor.py` (NEW, B256)
- `src/forest_soul_forge/daemon/routers/security.py` (NEW, B258)
- `src/forest_soul_forge/core/ground_truth.py` (NEW, B251)
- `src/forest_soul_forge/registry/tables/reality_anchor_corrections.py` (NEW, B255)
- `src/forest_soul_forge/tools/builtin/verify_claim.py` (NEW, B251)
- `src/forest_soul_forge/tools/builtin/security_scan.py` (NEW, B249)
- `src/forest_soul_forge/cli/passport_cmd.py` (NEW, B248)
- `src/forest_soul_forge/tools/governance_pipeline.py` (extended, B252/B255)
- `src/forest_soul_forge/tools/dispatcher.py` (extended, B252/B255)
- `src/forest_soul_forge/core/audit_chain.py` (8 new event types)
- `src/forest_soul_forge/registry/schema.py` (v20 bump)

### Frontend

- `frontend/js/reality-anchor.js` (NEW, B256)
- `frontend/js/security.js` (NEW, B258)
- `frontend/index.html` (2 new tabs + 2 new panels)
- `frontend/css/style.css` (+ ~230 lines for the two panes)
- `frontend/js/app.js` (import + start the two modules)

### Config catalogs (operator-asserted truth)

- `config/ground_truth.yaml` (NEW, 14 facts)
- `config/security_iocs.yaml` (NEW, 16 rules)
- `config/trait_tree.yaml` (added reality_anchor role)
- `config/genres.yaml` (added reality_anchor to guardian)
- `config/tool_catalog.yaml` (3 new tools + 1 new archetype kit)
- `config/constitution_templates.yaml` (added reality_anchor template)

### Tests

11 new test files (~145 tests):

- `tests/unit/test_daemon_passport.py` — endpoint tests (8)
- `tests/unit/test_cli_passport.py` — CLI tests (6)
- `tests/unit/test_security_scan.py` — scanner tool tests (20+)
- `tests/unit/test_install_scanner.py` — install gate (9)
- `tests/unit/test_ground_truth.py` — loader (~15)
- `tests/unit/test_verify_claim.py` — verifier tool (~20)
- `tests/unit/test_reality_anchor_step.py` — pipeline step (~20)
- `tests/unit/test_reality_anchor_role.py` — role + singleton (~10)
- `tests/unit/test_reality_anchor_turn.py` — conversation hook (~11)
- `tests/unit/test_reality_anchor_corrections.py` — corrections table (~25)
- `tests/unit/test_daemon_reality_anchor.py` — /reality-anchor/* router (~12)
- `tests/unit/test_daemon_security.py` — /security/* router (~11)
- `tests/unit/test_forge_stage_scanner.py` — forge-stage gate (~12)

### Diagnostic helpers (durable artifacts)

- `diagnose-import.command` — captures daemon import errors
  (bypasses start.command's stderr suppression). Created
  during the cryptography-dep incident, kept for future
  triage.
- `fix-cryptography-dep.command` — direct pip install for
  missing transitive dep.
- `diag-anchor-birth.command` — captures the actual 503
  reason from `/birth` calls.
- `diag-session-tests.command` — runs the session's new test
  files only, separating own-work failures from pre-existing.
- `run-session-tests.command` — full unit suite runner.
- `fix-and-rerun-tests.command` — installs numpy + reruns full
  suite (used after pre-existing collection failures from
  4 test modules that import numpy without it being in pyproject).

## What's NOT done

These remain queued from prior ADR work, untouched this
session:

- **ADR-0044 P7** — v0.6 tag (gated on external integrator
  validation per the ADR).
- **ADR-0050** — encryption at rest (drafted; no
  implementation).
- **ADR-0051** — per-tool subprocess sandbox (drafted; no
  implementation).
- **ADR-0052** — pluggable secrets storage (Proposed;
  substrate partially shipped for ADR-0049 keystore).
- **ADR-0054 T5b chat-thumbs UI** — operator-facing
  reinforcement widget on chat turns.
- **ADR-0055 Phases B/C/D** — marketplace federation,
  signing tools, telemetric scores.
- **ADR-0055 M6** — marketplace artifact signature
  verification.
- **pyproject.toml deps audit** — the cryptography + numpy
  incidents both showed that `pip install -e .` silently
  skips installing some transitive runtime deps. Fresh
  clones reproduce the issue. Worth investigating once.

## Reference timeline

| Commit | Burst | Headline |
|---|---|---|
| `398cb0a` | B248 | ADR-0061 closed: passport HTTP + CLI |
| `556eba6` | B249 | ADR-0062 T1-T3: IoC scanner |
| `0ed2ce1` | B250 | ADR-0062 T4: install-time gate |
| `79bced7` | B251 | ADR-0063 T1-T2: ground truth + verify_claim |
| `78b725d` | B252 | ADR-0063 T3: RealityAnchorStep |
| `7a83d20` | B253 | ADR-0063 T4: reality_anchor role + singleton |
| `1681630` | B254 | ADR-0063 T5: conversation pre-turn hook |
| `a97d781` | B255 | ADR-0063 T6: correction memory (schema v20) |
| `794d1fb` | B256 | **ADR-0063 CLOSED**: SoulUX Reality tab T7 |
| `c24f63d` | B256.1 | hotfix: trait-weight floor |
| `d588db7` | B256.2 | test-fixture fixes |
| `49ee965` | B257 | ADR-0062 T5: forge-stage scanner |
| `cd83e83` | B258 | **ADR-0062 CLOSED**: SoulUX Security tab T6 |

Head = `cd83e83`. All three session-arc ADRs (0061 / 0062
/ 0063) closed end-to-end with operator-facing surfaces.
