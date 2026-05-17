# Project conventions for Claude harness sessions

This file is loaded into context at the start of every harness
session via the auto-memory system. It captures the conventions
that have proved stable across iterations, so future sessions
don't re-derive them from scratch.

## Operating principles

**§0 Hippocratic gate — every removal verified before action:**

Before removing, deleting, or "ripping out" anything, the change
must pass this 4-step gate:

1. **Prove harm** — what test fails, what feature breaks, what
   error surfaces, what user is misled? If "no concrete harm"
   surfaces, don't act.
2. **Prove non-load-bearing** — grep for imports/references; check
   `git blame` for "placeholder per ADR-X" intent; scan ADRs for
   forward references. Anything labeled "for v0.3+" stays with a
   comment explaining why.
3. **Prove the alternative is strictly better** — could we fix in
   place instead? Could we deprecate-and-migrate instead of
   delete? Removal is the last resort, not the default.
4. **If 1+2+3 all pass:** remove, AND record the removal in
   CHANGELOG + the relevant ADR + an audit-doc entry so the
   decision is traceable.

If any of 1+2+3 fails: leave in place + document why. Add a
comment, an ADR note, or a STATE.md callout so future sessions
know this thing is intentional, not abandoned.

**Default for "looks unused":** keep with a comment, not delete.

**§1 Trust-surface decomposition rule — ADR-0040:**

Before extracting code into a new file, or before deciding NOT to
extract a large file, check the trust-surface count:

1. **Count trust surfaces** in the file. A trust surface is one
   coherent area of governance — agent creation, voice regeneration,
   archival lifecycle, etc. Methods/endpoints that share the same
   governance discipline (same write-lock semantics, same scope
   checks, same audit event family) are ONE surface.
2. **One surface = leave alone, even if large.** A 1000-LoC file
   that owns one cohesive surface is fine; AI-grade governance
   safeties (file-grained `allowed_paths`) work at file granularity.
3. **Multiple surfaces = decompose.** Each surface gets its own
   file so a constitution can grant `allowed_paths` to one without
   inheriting the others. The decomposition is the governance
   delivery mechanism.
4. **Pattern-match to existing decomps.** Class-based: per-trust-
   surface mixin (memory.py → memory/ package, Bursts 72-76).
   Router-based: per-endpoint sub-router under a package facade
   (writes.py → writes/ package, Bursts 77-80). Pick the shape
   that matches the parent's structure.

The §0 Hippocratic gate still applies — if the decomposition has
no concrete governance harm to point to, leave in place.

**§2 Dispatcher wiring discipline — B350 lesson:**

Every subsystem the dispatcher claims to expose via `ToolContext`
needs THREE things, or it's silently dead code on the HTTP path:

1. **A typed field on `ToolContext`** in
   `src/forest_soul_forge/tools/base.py`. Default `None`. Inline
   comment explaining what the subsystem is and when the tool
   should refuse cleanly vs. raise.
2. **A population line in `dispatcher.py`** inside the
   `ToolContext(...)` constructor call (~line 999). Pattern:
   `subsystem_name=self.attr_holding_the_subsystem`.
3. **A probe in `dev-tools/diagnostic/section-06-ctx-wiring.command`**
   — one entry in the `SUBSYSTEMS` list that dispatches a tool
   depending on the subsystem and asserts the response doesn't
   contain a "not wired" error message.

Missing any one of these = the tool that depends on the subsystem
passes unit tests (test fixture constructs `ToolContext` by hand)
but raises `ToolValidationError` on the HTTP path. Discovered
2026-05-17: `audit_chain_verify.v1` had been dead since
ADR-0033 Phase B1 because nobody had written the dispatcher wire
line. Unit tests passed for years. The bug only surfaced when D3
Phase A's `archive_evidence.v1` skill became the first real
consumer in live verification.

**§3 Bare version strings in tool registration — B353 lesson:**

In `src/forest_soul_forge/tools/builtin/<tool>.py`, the
`_VERSION` constant MUST be a bare numeric string like `"1"`,
NOT a `v`-prefixed string like `"v1"`. The registry's key
composer at `tools/base.py:_key` does `f"{name}.v{version}"` —
passing `"v1"` produces `<tool>.vv1` in the registry while the
catalog has `<tool>.v1`, and `tool_runtime` startup_diagnostics
flags it as a registry/catalog mismatch.

Every other builtin uses `_VERSION = "1"`. Mirror that exactly
when adding a new builtin tool. The diagnostic harness section 04
catches this drift automatically; if you see a tool_runtime
warning after adding a new tool, this is the first thing to check.

## Verification discipline

- After every code change: run the relevant test file
- After every batch of changes: run the full suite
- Don't claim "done" without proof
- Document any xfail with a specific reason in the marker — never
  use `pytest.skip(reason="env-mismatch")` for a real bug
- An xfail with a tracked reason is more honest than a skip; a
  skip with a falsified reason is worse than either

## Phase letter map

The repo uses single-letter prefixes for major work tracks:

| Letter | Track | ADRs |
|:---:|---|---|
| **A** | (audit) | tracked in `docs/audits/` |
| **B** | Phase B / B1-B3 — Security Swarm tools | ADR-0033 |
| **C** | Phase C — open-web (also Phase C decomposition) | ADR-003X |
| **D** | Phase D — Security Swarm bring-up | ADR-0033 |
| **E** | Phase E — Security Swarm smoke | ADR-0033 |
| **G** | G-track — open-web genres | ADR-003X |
| **K** | K-track — ADR-003X tranches (K1=verified memory, K2=secrets, K4=mcp_call, K5=chronicle, K6=hardware) | ADR-003X |
| **R** | R-track — refactors (R2=birth_pipeline, R3=governance_pipeline, R4=registry tables) | various |
| **SW** | SW-track — software-engineering triune | ADR-0034 |
| **T** | T-track — tool-execution-runtime tranches | ADR-0019 |
| **Y** | Y-track — conversation runtime (Y1-Y7) | ADR-003Y |

Audit docs use ISO-date prefixes (`2026-04-30-*`); audits are the
canonical timeline.

## File creation conventions

- **No spurious docs.** Don't create README/MEMORY/PROGRESS files
  unless explicitly requested or required.
- **Audit-grade prose** — comments explain WHY, not WHAT. Code
  shows what; comments show why we made this choice.
- **§0 reasoning visible** — when removing or changing
  load-bearing code, comment the §0 verification chain in-place
  (or link to the audit doc that records it).
- **One canonical timeline** — `docs/audits/` is the canonical
  timeline of architectural changes. Phase A audit (2026-04-30),
  C-1 dissection (2026-04-30), etc. New audits go there with
  ISO-date prefixes.

## Test fixture pattern

The test suite uses a shared `tests/unit/conftest.py` with
`seed_stub_agent(registry, instance_id)` for FK-seeding agents
in unit tests. SQLite has FK enforcement enabled
(`PRAGMA foreign_keys=ON`), so any test that exercises a table
with a FK to `agents.instance_id` (memory_entries, tool_calls,
tool_call_pending_approvals, tool_call_counters, conversations'
participants) MUST seed the agent row first.

Phase A (2026-04-30) traced 43 FK-constraint failures across the
unit suite to this single missing-seed pattern. The shared helper
is the durable fix.

## Operator constraints

- **Alex is the orchestrator and final decision maker.** I am the
  brains and development arm. I propose; he decides.
- **No BS, no ego stroking, no lip service.** Concrete claims
  only, every assertion grounded in actual file inspection or
  test output.
- **Take direct control on his Mac when executing approved tasks.**
  Don't hand back a test plan; drive the runtime, watch what
  happens, course-correct.
- **Make commits followable.** Each commit = one coherent change
  with a descriptive message. No "random stuff," no branches off
  the main vision.
- **Read every line during deep dives.** When asked to understand
  the project, start at ADR-0001 and work forward; drive the
  running system; don't ask abstract questions before doing the
  reading.

## Sandbox-vs-host gotchas

- The harness runs in a Linux sandbox. The user's host is macOS.
- Sandbox Python is 3.10; project requires 3.11+. Some tests will
  show "skipped" or "xfailed" with reasons related to this.
- Sandbox can't always remove `.git/index.lock` (Operation not
  permitted). When this happens, the user has to run
  `clean-git-locks.command` from his terminal. Commits accumulate
  in the working tree until the lock clears.
- Sandbox SQLite is 3.37.2 (modern); host SQLite is whatever
  Python 3.11 ships (likely 3.40+). The v6→v7 migration test is
  xfailed because of an interaction between sandbox SQLite + the
  test setup, NOT because the production migration is broken.
- The sandbox can `mcp__workspace__bash` for shell commands, but
  the working dir + mounts are different from the host paths.
  Always use absolute paths.

## Architectural invariants (don't break these)

- **Audit chain is append-only and hash-linked.** Every state
  change → one chain entry. The chain is the source of truth;
  the registry is rebuildable from it.
- **DNA is content-addressed.** Same trait profile → same DNA.
  Don't ever change the DNA derivation without a major version
  bump and a migration plan.
- **Constitution hash is immutable per agent.** A born agent's
  constitution hash is bound to its identity; recomputing it
  invalidates verification.
- **`body_hash` survives Y7 purge.** After lazy summarization
  removes a turn body, `body_hash` (SHA-256 of the original)
  stays for tamper-evidence.
- **Single-writer SQLite discipline.** All write paths go through
  `app.state.write_lock` (a `threading.RLock`). Don't add new
  writers that bypass this.
- **Genre kit-tier ceiling.** A role's resolved tools must not
  exceed `genre.max_side_effects`. The check fires at birth time
  AND at runtime via `GenreFloorStep` in the governance pipeline.
- **One file, one trust surface (ADR-0040).** Files that own a
  single governance area can grow to whatever size their cohesion
  warrants. Files with multiple trust surfaces MUST decompose so
  `allowed_paths` can grant scoped access. memory/ and writes/
  are the canonical decompositions; new code follows the same
  pattern.
- **Live audit chain is at `examples/audit_chain.jsonl`, not
  `data/audit_chain.jsonl`.** Per `daemon/config.py` the default
  `audit_chain_path` points to examples/. Override via
  `FSF_AUDIT_CHAIN_PATH`. The data/ chain is a stale dev fixture.
  Verify chain integrity via `dev-tools/check-drift.sh` (which
  also runs every numeric drift check before tagging).

## Live-test driver gotchas (Run 001 lessons)

Two patterns surfaced during the FizzBuzz autonomous loop test
that future scenario runs should know — both are silent failures
that look like the loop is broken when actually the driver is:

1. **`python3 - <<'PYEOF'` makes the heredoc replace stdin.** When
   you need `sys.stdin.read()` to work on piped input, use
   `python3 -c '...'` instead. The heredoc form turns the script
   body INTO stdin, so your read returns nothing.
2. **`curl -sf` swallows error response bodies.** When you need
   to debug a 4xx/5xx, drop `-f` so the body surfaces. The
   `|| echo '{}'` fallback hides the actual failure shape. Add
   `tool_version` and unique `session_id` to every
   `/agents/{id}/tools/call` request — they're required by
   `ToolCallRequest` (see `src/forest_soul_forge/daemon/schemas/dispatch.py`).

Reference driver: `live-test-fizzbuzz.command`. Bug ledger encoded
in its header — five fixes captured so future scenarios reuse the
proven pattern.

## Things to look up rather than guess

- Tool catalog: `config/tool_catalog.yaml`
- Trait tree: `config/trait_tree.yaml`
- Genres: `config/genres.yaml`
- Constitution templates: `config/constitution_templates.yaml`
- ADR index: `docs/decisions/`
- Architectural layout: `docs/architecture/layout.md`
- Current state snapshot: `STATE.md`

If a number changes (LoC, tool count, role count, etc.), measure
it from disk. Don't quote a count from memory or an older audit
without re-verifying.
