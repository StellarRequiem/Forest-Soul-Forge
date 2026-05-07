You are Smith in WORK mode (ADR-0056). This is cycle 2, plan v1.

Cycle 1 closed green: you produced 4 unit tests for the
`GET /conversations/{id}/last-shortcut` endpoint (cycle-1.6 verbatim
output, applied with operator helper kwargs fix). The test suite
caught a B195 bug — the route was double-reversing audit.tail
output and returning the OLDEST matching shortcut instead of the
newest. Real value-add. Cycle 1 also surfaced two prompt-engineering
findings (prior-cycle threading + verbatim wrappers) which are now
wired into a reusable operator helper at dev-tools/cycle_dispatch.py.

This cycle picks a NEW undertested router endpoint and produces
the same shape of deliverable.

TARGET
------

Write unit tests for `POST /agents/{instance_id}/cycles/{cycle_id}/decision`
in `src/forest_soul_forge/daemon/routers/cycles.py` (lines 540-720).

The endpoint is the operator's review surface for closing a Smith
cycle. It supports three action modes (approve / deny / counter)
plus several error paths. Currently `tests/unit/test_cycles_router.py`
has tests for the GET endpoints (list cycles, cycle detail) but
NOTHING for the decision endpoint.

The endpoint signature you need to test is in <copy_verbatim id="endpoint">.
The existing test_cycles_router.py fixture pattern you should
EXTEND (not redefine) is in <copy_verbatim id="fixture">.

DELIVERABLES
------------

A test file at `tests/unit/test_cycles_decision.py` (separate from
test_cycles_router.py to keep modules under a single trust surface
per ADR-0040, and because test_cycles_router.py is already 200+
LoC). Reuse the helpers from test_cycles_router.py via import:

    from tests.unit.test_cycles_router import (
        API_TOKEN,
        INSTANCE_ID,
        _build_client,
        _git,
    )

OR redefine them if the existing helpers don't carry what you need
(e.g. allow_write_endpoints=True is required for audit_chain init —
the existing _build_client passes False which works for GET tests
but will fail for the decision endpoint because it appends to the
chain). If you redefine, give your helper a different name to avoid
import collision.

Cover at minimum:

1. **approve clean merge** — cycle-1 has a CYCLE_REPORT.md, no
   conflicting files. POST with action=approve returns 200, audit
   chain has one experimenter_cycle_decision event with
   action="approve" and a merge_commit_sha string.
2. **approve merge conflict** — set up cycle-1 + a conflicting
   change on main (modify the same file). POST should return 409
   with "conflict" in detail; audit event NOT emitted (or emitted
   with action error — verify which).
3. **deny with delete_branch=true** — branch goes away from the
   workspace's git refs after the call.
4. **deny with delete_branch=false** — branch preserved.
5. **counter** — note recorded in audit chain event.
6. **400 invalid cycle_id** — `cycle_id` like "foo" or "../etc"
   should hit the path-traversal regex check (line 582).

Optional:

7. **404 unknown agent** — instance_id not in registry.
8. **404 branch not in workspace** — cycle_id parses but no such
   branch exists.

GROUND TRUTH — INTERFACES
--------------------------

`audit.append` returns a `ChainEntry` with `.seq`, `.timestamp`,
`event_type`, `event_data` — same as cycle 1. To verify the audit
event was emitted, use `client.app.state.audit_chain.tail(20)` and
filter by `event_type == "experimenter_cycle_decision"`.

The endpoint takes a JSON body with shape:

    {
      "action": "approve" | "deny" | "counter",
      "note": str | None,
      "delete_branch": bool  # only honored for deny
    }

`registry.bootstrap(...)` and `seed_stub_agent(reg, INSTANCE_ID)`
plug the agent FK row in (per CLAUDE.md). Test_cycles_router.py
already does this — match its pattern.

OUTPUT FORMAT
-------------

1. **Target** — one sentence.
2. **Test plan** — list the 6+ test methods you'll write, one
   line each (method name + what it asserts).
3. **Concrete deliverable** — full Python file content for
   `tests/unit/test_cycles_decision.py`, ready to apply.
4. **Verification** — exact pytest command + expected green
   summary.
5. **Risks / blast radius** — one paragraph. Where might the
   test diverge from the actual endpoint? What interface
   assumption is hardest to verify without running?

Keep total output under 2200 words. Be specific. The operator
will diff your output against the actual endpoint code and apply
with as few corrections as possible. Cycle 1.6 succeeded because
you copied <copy_verbatim> blocks verbatim — same expectation
applies here for the endpoint signature and fixture imports.
