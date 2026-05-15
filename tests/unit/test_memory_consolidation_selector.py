"""ADR-0074 T2 (B302) — ConsolidationSelector tests.

Pure-function tests against ``select_consolidation_candidates``.
The selector is a SQL query over the B294 schema-v23 columns;
tests build an in-memory SQLite, apply migrations[23], seed rows
with controlled (age, layer, claim_type, state, deleted) tuples,
and verify the filter + ordering + batch-size cap.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from forest_soul_forge.core.memory_consolidation import (
    ConsolidationPolicy,
    select_consolidation_candidates,
)
from forest_soul_forge.registry.schema import MIGRATIONS


NOW = datetime(2026, 5, 14, tzinfo=timezone.utc)


def _fresh_db() -> sqlite3.Connection:
    """Build an in-memory SQLite at schema v23 with a single
    pre-seeded agent 'a1'."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("CREATE TABLE agents (instance_id TEXT PRIMARY KEY)")
    # Pre-v23 shape of memory_entries (we apply MIGRATIONS[23]
    # below to bring it forward).
    conn.execute(
        """
        CREATE TABLE memory_entries (
            entry_id TEXT PRIMARY KEY,
            instance_id TEXT NOT NULL,
            agent_dna TEXT NOT NULL,
            layer TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT 'private',
            content TEXT NOT NULL,
            content_digest TEXT NOT NULL,
            tags_json TEXT NOT NULL DEFAULT '[]',
            consented_to_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            deleted_at TEXT,
            disclosed_from_entry TEXT,
            disclosed_summary TEXT,
            disclosed_at TEXT,
            claim_type TEXT NOT NULL DEFAULT 'observation',
            confidence TEXT NOT NULL DEFAULT 'medium',
            last_challenged_at TEXT,
            content_encrypted INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    for stmt in MIGRATIONS[23]:
        conn.execute(stmt)
    conn.execute("INSERT INTO agents (instance_id) VALUES ('a1')")
    return conn


def _insert(
    conn: sqlite3.Connection,
    entry_id: str,
    *,
    age_days: int = 20,
    layer: str = "episodic",
    claim_type: str = "observation",
    state: str = "pending",
    deleted: bool = False,
) -> None:
    """Seed one row with the chosen filter inputs. created_at is
    derived from `age_days` relative to NOW so tests stay
    deterministic."""
    created = (NOW - timedelta(days=age_days)).isoformat()
    deleted_at = (NOW - timedelta(days=1)).isoformat() if deleted else None
    conn.execute(
        "INSERT INTO memory_entries ("
        "  entry_id, instance_id, agent_dna, layer, content, "
        "  content_digest, created_at, deleted_at, claim_type, "
        "  consolidation_state"
        ") VALUES (?, 'a1', 'dna', ?, 'c', 'd', ?, ?, ?, ?)",
        (entry_id, layer, created, deleted_at, claim_type, state),
    )


# ---------------------------------------------------------------------------
# Empty / happy path
# ---------------------------------------------------------------------------

def test_empty_db_returns_empty_list():
    conn = _fresh_db()
    assert select_consolidation_candidates(
        conn, policy=ConsolidationPolicy(), now=NOW,
    ) == []


def test_aged_episodic_observation_is_eligible():
    conn = _fresh_db()
    _insert(conn, "e_old", age_days=20)
    assert select_consolidation_candidates(
        conn, policy=ConsolidationPolicy(), now=NOW,
    ) == ["e_old"]


# ---------------------------------------------------------------------------
# Filter gates
# ---------------------------------------------------------------------------

def test_young_entries_below_min_age_filtered():
    """Default min_age_days=14; a 5-day-old entry is excluded."""
    conn = _fresh_db()
    _insert(conn, "e_young", age_days=5)
    assert select_consolidation_candidates(
        conn, policy=ConsolidationPolicy(), now=NOW,
    ) == []


def test_min_age_zero_includes_brand_new_entries():
    """Setting min_age_days=0 means anything pending is eligible —
    use case is operator forcing a one-off pass after a backfill."""
    conn = _fresh_db()
    _insert(conn, "e_now", age_days=0)
    out = select_consolidation_candidates(
        conn, policy=ConsolidationPolicy(min_age_days=0), now=NOW,
    )
    # The created_at-strict-less-than-cutoff filter still requires
    # the row to be strictly older than `now`, so age=0 (exactly NOW)
    # is excluded. age_days=1 with min_age_days=0 would pass.
    assert out == []
    # Confirm the 1-day case works.
    _insert(conn, "e_1d", age_days=1)
    out = select_consolidation_candidates(
        conn, policy=ConsolidationPolicy(min_age_days=0), now=NOW,
    )
    assert out == ["e_1d"]


def test_non_eligible_layers_filtered():
    conn = _fresh_db()
    _insert(conn, "e_working", age_days=30, layer="working")
    assert select_consolidation_candidates(
        conn, policy=ConsolidationPolicy(), now=NOW,
    ) == []


def test_custom_eligible_layers_honored():
    """Operator widens the policy to include working-layer entries."""
    conn = _fresh_db()
    _insert(conn, "e_working", age_days=30, layer="working")
    out = select_consolidation_candidates(
        conn,
        policy=ConsolidationPolicy(
            eligible_layers=("episodic", "working"),
        ),
        now=NOW,
    )
    assert out == ["e_working"]


def test_non_eligible_claim_types_filtered():
    """Default policy excludes promise + preference + agent_inference."""
    conn = _fresh_db()
    for ct in ("promise", "preference", "agent_inference", "external_fact"):
        _insert(conn, f"e_{ct}", age_days=30, claim_type=ct)
    assert select_consolidation_candidates(
        conn, policy=ConsolidationPolicy(), now=NOW,
    ) == []


def test_non_pending_states_all_filtered():
    """consolidated / summary / pinned / purged states all excluded —
    only 'pending' rows are eligible for the next pass."""
    conn = _fresh_db()
    for state in ("consolidated", "summary", "pinned", "purged"):
        _insert(conn, f"e_{state}", age_days=30, state=state)
    assert select_consolidation_candidates(
        conn, policy=ConsolidationPolicy(), now=NOW,
    ) == []


def test_deleted_entries_filtered():
    """ADR-0022 deleted_at marker excludes the row even when it's
    pending. The runner doesn't fold tombstones."""
    conn = _fresh_db()
    _insert(conn, "e_dead", age_days=30, deleted=True)
    assert select_consolidation_candidates(
        conn, policy=ConsolidationPolicy(), now=NOW,
    ) == []


# ---------------------------------------------------------------------------
# Ordering + batch-size
# ---------------------------------------------------------------------------

def test_returns_oldest_first():
    """ASC created_at — FIFO. Operators expect first-in-first-folded."""
    conn = _fresh_db()
    _insert(conn, "e_30d", age_days=30)
    _insert(conn, "e_25d", age_days=25)
    _insert(conn, "e_60d", age_days=60)
    out = select_consolidation_candidates(
        conn, policy=ConsolidationPolicy(), now=NOW,
    )
    assert out == ["e_60d", "e_30d", "e_25d"]


def test_batch_size_cap_honored():
    """max_batch_size caps the result length even when more rows
    are eligible. Picks the oldest N."""
    conn = _fresh_db()
    for i in range(10):
        _insert(conn, f"e_{i:02}", age_days=30 + i)
    out = select_consolidation_candidates(
        conn,
        policy=ConsolidationPolicy(max_batch_size=3),
        now=NOW,
    )
    assert len(out) == 3
    # Oldest 3: i=9 (39d), i=8 (38d), i=7 (37d).
    assert out == ["e_09", "e_08", "e_07"]


# ---------------------------------------------------------------------------
# Policy validation
# ---------------------------------------------------------------------------

def test_policy_rejects_negative_min_age():
    with pytest.raises(ValueError, match="min_age_days"):
        ConsolidationPolicy(min_age_days=-1)


def test_policy_rejects_zero_batch_size():
    with pytest.raises(ValueError, match="max_batch_size"):
        ConsolidationPolicy(max_batch_size=0)


def test_policy_rejects_empty_eligible_layers():
    with pytest.raises(ValueError, match="eligible_layers"):
        ConsolidationPolicy(eligible_layers=())


def test_policy_rejects_empty_eligible_claim_types():
    with pytest.raises(ValueError, match="eligible_claim_types"):
        ConsolidationPolicy(eligible_claim_types=())


def test_policy_defaults_match_adr_specification():
    """Pin the ADR-0074 T2 defaults at the construction site so a
    refactor that changes them becomes a visible diff."""
    p = ConsolidationPolicy()
    assert p.min_age_days == 14
    assert p.max_batch_size == 200
    assert p.eligible_layers == ("episodic",)
    assert p.eligible_claim_types == ("observation", "user_statement")


# ---------------------------------------------------------------------------
# ADR-0074 T3 (B306) — ConsolidationSummarizer
# ---------------------------------------------------------------------------

import asyncio

from forest_soul_forge.core.memory_consolidation import (
    ConsolidationSummarizerError,
    SourceEntry,
    SummaryDraft,
    _render_summary_prompt,
    summarize_consolidation_batch,
)


class _MockProvider:
    """Deterministic mock provider — records the prompt + kwargs it
    sees, returns a fixed string."""

    def __init__(self, response: str = "mock summary"):
        self.response = response
        self.last_prompt: str | None = None
        self.last_kwargs: dict | None = None

    async def complete(self, prompt, *, task_kind=None, max_tokens=None, **kw):
        self.last_prompt = prompt
        self.last_kwargs = {"task_kind": task_kind, "max_tokens": max_tokens}
        return self.response


def _sources() -> list[SourceEntry]:
    return [
        SourceEntry(
            entry_id="e1", content="Met with Mira about Q3 plan.",
            layer="episodic", claim_type="observation",
            created_at="2026-04-10T09:00:00Z",
        ),
        SourceEntry(
            entry_id="e2", content="Mira pushed for the engineering hire.",
            layer="episodic", claim_type="observation",
            created_at="2026-04-15T14:00:00Z",
        ),
    ]


# Happy path

def test_summarize_returns_summary_draft_with_lineage():
    """Successful summarization produces a SummaryDraft whose
    source_entry_ids matches the input batch."""
    p = _MockProvider("Mira drove the eng-hire decision.")
    out = asyncio.run(
        summarize_consolidation_batch(_sources(), provider=p),
    )
    assert isinstance(out, SummaryDraft)
    assert out.content == "Mira drove the eng-hire decision."
    assert out.source_entry_ids == ("e1", "e2")
    assert out.layer == "episodic"
    assert out.claim_type == "agent_inference"


def test_summarize_prompt_carries_observations_and_layer():
    """The prompt the provider receives includes each observation
    + its timestamp + the layer header. Pin so a prompt refactor
    can't silently drop fields."""
    p = _MockProvider("summary")
    asyncio.run(summarize_consolidation_batch(_sources(), provider=p))
    assert p.last_prompt is not None
    assert "Layer: episodic" in p.last_prompt
    assert "[2026-04-10T09:00:00Z]" in p.last_prompt
    assert "Met with Mira" in p.last_prompt
    assert "Mira pushed" in p.last_prompt
    assert "Summary:" in p.last_prompt


def test_summarize_forwards_max_tokens_and_task_kind():
    """The provider sees the operator-tunable max_tokens and the
    canonical TaskKind.GENERATE (or the 'generate' fallback when
    daemon providers aren't importable)."""
    p = _MockProvider("summary")
    asyncio.run(
        summarize_consolidation_batch(
            _sources(), provider=p, max_tokens=150,
        ),
    )
    assert p.last_kwargs["max_tokens"] == 150
    tk = p.last_kwargs["task_kind"]
    # Either the enum value (when import works) or the string
    # fallback (when it doesn't).
    assert str(getattr(tk, "value", tk)) == "generate"


# Error paths

def test_summarize_refuses_empty_batch():
    p = _MockProvider()
    with pytest.raises(ConsolidationSummarizerError, match="empty source"):
        asyncio.run(summarize_consolidation_batch([], provider=p))


def test_summarize_refuses_empty_content():
    p = _MockProvider()
    src = [SourceEntry(
        entry_id="e_bad", content="",
        layer="episodic", claim_type="observation",
        created_at="2026-04-10T00:00:00Z",
    )]
    with pytest.raises(ConsolidationSummarizerError, match="empty content"):
        asyncio.run(summarize_consolidation_batch(src, provider=p))


def test_summarize_refuses_multi_layer_batch():
    """The runner enforces single-layer batches; the summarizer
    catches a multi-layer batch as a composition bug."""
    p = _MockProvider()
    src = [
        SourceEntry("e1", "a", "episodic", "observation", "2026-04-10T00:00:00Z"),
        SourceEntry("e2", "b", "working", "observation", "2026-04-11T00:00:00Z"),
    ]
    with pytest.raises(
        ConsolidationSummarizerError, match="multiple layers",
    ):
        asyncio.run(summarize_consolidation_batch(src, provider=p))


def test_summarize_wraps_provider_error():
    """A raised exception from provider.complete becomes a
    ConsolidationSummarizerError so the runner's circuit-breaker
    counts toward max_consecutive_failures correctly."""
    class Broken:
        async def complete(self, *a, **kw):
            raise RuntimeError("boom")
    with pytest.raises(
        ConsolidationSummarizerError, match="provider.complete failed",
    ):
        asyncio.run(summarize_consolidation_batch(
            _sources(), provider=Broken(),
        ))


def test_summarize_refuses_empty_provider_response():
    """A whitespace-only response is functionally empty and would
    produce a junk summary row. Refuse."""
    p = _MockProvider("   ")
    with pytest.raises(
        ConsolidationSummarizerError, match="empty summary",
    ):
        asyncio.run(summarize_consolidation_batch(_sources(), provider=p))


def test_summarize_strips_whitespace_from_response():
    """Provider responses get leading/trailing whitespace stripped
    so audit-chain digests are stable across whitespace drift."""
    p = _MockProvider("  Mira drove the decision.\n\n")
    out = asyncio.run(
        summarize_consolidation_batch(_sources(), provider=p),
    )
    assert out.content == "Mira drove the decision."


def test_summary_draft_is_frozen():
    """SummaryDraft mutations should raise so the runner can't
    accidentally corrupt the lineage record between summarize +
    insert."""
    d = SummaryDraft(content="x", source_entry_ids=("e1",), layer="episodic")
    with pytest.raises(Exception):
        d.content = "y"  # noqa


# ---------------------------------------------------------------------------
# ADR-0074 T4 (B307) — run_consolidation_pass
# ---------------------------------------------------------------------------

from forest_soul_forge.core.memory_consolidation import (
    ConsolidationRunResult,
    run_consolidation_pass,
)


class _MockChain:
    """Captures audit.append calls so assertions can target them."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def append(self, event_type, payload, *, agent_dna=None):
        self.events.append((event_type, dict(payload)))


def _seed_extra(
    conn: sqlite3.Connection,
    entry_id: str,
    *,
    instance_id: str = "a1",
    age_days: int = 30,
    layer: str = "episodic",
    claim_type: str = "observation",
    state: str = "pending",
    encrypted: int = 0,
    content: str | None = None,
) -> None:
    """Seed a memory row with the runner-relevant fields."""
    created = (NOW - timedelta(days=age_days)).isoformat()
    conn.execute(
        "INSERT INTO memory_entries ("
        "  entry_id, instance_id, agent_dna, layer, content, "
        "  content_digest, created_at, claim_type, "
        "  consolidation_state, content_encrypted"
        ") VALUES (?, ?, 'dna', ?, ?, 'd', ?, ?, ?, ?)",
        (
            entry_id, instance_id, layer,
            content or f"content_{entry_id}",
            created, claim_type, state, encrypted,
        ),
    )


def _two_agents_db() -> sqlite3.Connection:
    """Build a v23 DB with two agents pre-seeded."""
    conn = _fresh_db()
    conn.execute("INSERT INTO agents (instance_id) VALUES ('agent_b')")
    return conn


def test_runner_end_to_end_two_agents():
    """Happy path: two agents, two groups, two summaries, all
    sources flipped to consolidated, audit events emitted."""
    conn = _two_agents_db()
    # 3 episodic memories for agent a1, 2 for agent_b.
    for i in range(3):
        _seed_extra(conn, f"a_{i}", instance_id="a1", age_days=30 + i)
    for i in range(2):
        _seed_extra(conn, f"b_{i}", instance_id="agent_b", age_days=30 + i)

    chain = _MockChain()
    result = asyncio.run(run_consolidation_pass(
        conn, policy=ConsolidationPolicy(),
        provider=_MockProvider("summary"),
        audit_chain=chain, now=NOW,
    ))

    assert isinstance(result, ConsolidationRunResult)
    assert result.batches_processed == 2
    assert result.summaries_created == 2
    assert result.sources_consolidated == 5
    assert result.errors == ()

    # DB state — sources flipped, summaries inserted.
    states = [
        r[0] for r in conn.execute(
            "SELECT consolidation_state FROM memory_entries"
        ).fetchall()
    ]
    assert states.count("consolidated") == 5
    assert states.count("summary") == 2


def test_runner_lineage_links_consolidated_to_summary():
    """Every consolidated row has consolidated_into pointing at
    its summary, and the summary row's instance_id matches its
    sources' instance_id."""
    conn = _fresh_db()
    for i in range(3):
        _seed_extra(conn, f"a_{i}", instance_id="a1", age_days=30 + i)
    chain = _MockChain()
    asyncio.run(run_consolidation_pass(
        conn, policy=ConsolidationPolicy(),
        provider=_MockProvider("summary"),
        audit_chain=chain, now=NOW,
    ))
    rows = conn.execute(
        "SELECT entry_id, consolidation_state, consolidated_into, "
        "instance_id FROM memory_entries"
    ).fetchall()
    by_state = {r[0]: r for r in rows}
    summary_row = [r for r in rows if r[1] == "summary"][0]
    summary_id = summary_row[0]
    for r in rows:
        if r[1] == "consolidated":
            assert r[2] == summary_id
            assert r[3] == summary_row[3]  # same instance_id


def test_runner_emits_bookend_audit_events():
    """Run emits exactly one run_started + one run_completed
    bracketing per-entry memory_consolidated events. All three
    event types share the same run_id."""
    conn = _fresh_db()
    for i in range(2):
        _seed_extra(conn, f"a_{i}", instance_id="a1", age_days=30 + i)
    chain = _MockChain()
    asyncio.run(run_consolidation_pass(
        conn, policy=ConsolidationPolicy(),
        provider=_MockProvider(),
        audit_chain=chain, now=NOW,
    ))
    types = [e[0] for e in chain.events]
    assert types[0] == "memory_consolidation_run_started"
    assert types[-1] == "memory_consolidation_run_completed"
    per_entry = [e for e in chain.events if e[0] == "memory_consolidated"]
    assert len(per_entry) == 2
    run_id = chain.events[0][1]["run_id"]
    assert all(e[1]["run_id"] == run_id for e in chain.events)


def test_runner_empty_pass_still_emits_bookends():
    """When no candidates match the policy, the run still emits
    run_started + run_completed — operator sees the pass actually
    happened (and saw nothing to do) instead of a chain gap."""
    conn = _fresh_db()
    chain = _MockChain()
    result = asyncio.run(run_consolidation_pass(
        conn, policy=ConsolidationPolicy(),
        provider=_MockProvider(),
        audit_chain=chain, now=NOW,
    ))
    assert result.batches_processed == 0
    assert result.summaries_created == 0
    assert [e[0] for e in chain.events] == [
        "memory_consolidation_run_started",
        "memory_consolidation_run_completed",
    ]


def test_runner_skips_encrypted_sources():
    """Encrypted sources (content_encrypted=1) stay pending — the
    runner has no decryption key. This is a forward-compat skip
    until a tranche wires key access."""
    conn = _fresh_db()
    _seed_extra(conn, "e_plain", age_days=30)
    _seed_extra(conn, "e_encrypted", age_days=30, encrypted=1)
    chain = _MockChain()
    result = asyncio.run(run_consolidation_pass(
        conn, policy=ConsolidationPolicy(),
        provider=_MockProvider(),
        audit_chain=chain, now=NOW,
    ))
    assert result.sources_consolidated == 1
    enc_state = conn.execute(
        "SELECT consolidation_state FROM memory_entries "
        "WHERE entry_id='e_encrypted'"
    ).fetchone()[0]
    assert enc_state == "pending"


def test_runner_provider_failure_leaves_sources_pending():
    """A provider exception accumulates as a soft error and the
    group's sources stay pending (the with-conn rollback
    guarantees no half-applied state)."""
    class _Broken:
        async def complete(self, *a, **kw):
            raise RuntimeError("provider down")
    conn = _fresh_db()
    for i in range(2):
        _seed_extra(conn, f"a_{i}", age_days=30 + i)
    chain = _MockChain()
    result = asyncio.run(run_consolidation_pass(
        conn, policy=ConsolidationPolicy(),
        provider=_Broken(),
        audit_chain=chain, now=NOW,
    ))
    assert result.summaries_created == 0
    assert len(result.errors) == 1
    assert "provider" in result.errors[0][2]
    states = [
        r[0] for r in conn.execute(
            "SELECT consolidation_state FROM memory_entries"
        ).fetchall()
    ]
    assert set(states) == {"pending"}


def test_runner_partial_success_one_group_succeeds_one_errors():
    """A flaky provider can succeed for one group and fail for
    another. The successful group's writes commit; the failed
    group's writes don't."""
    class _Flaky:
        def __init__(self):
            self.calls = 0

        async def complete(self, *a, **kw):
            self.calls += 1
            if self.calls == 1:
                return "summary"
            raise RuntimeError("flaky on call 2")

    conn = _two_agents_db()
    for i in range(2):
        _seed_extra(conn, f"a_{i}", instance_id="a1", age_days=30 + i)
    for i in range(2):
        _seed_extra(conn, f"b_{i}", instance_id="agent_b", age_days=30 + i)

    chain = _MockChain()
    result = asyncio.run(run_consolidation_pass(
        conn, policy=ConsolidationPolicy(),
        provider=_Flaky(),
        audit_chain=chain, now=NOW,
    ))
    assert result.summaries_created == 1
    assert len(result.errors) == 1
    # 2 sources consolidated from the successful group + 2 still
    # pending from the failed group.
    states = [
        r[0] for r in conn.execute(
            "SELECT consolidation_state FROM memory_entries"
        ).fetchall()
    ]
    assert states.count("consolidated") == 2
    assert states.count("pending") == 2
    assert states.count("summary") == 1


def test_runner_summary_row_has_agent_inference_claim_type():
    """ADR-0027 amendment alignment: summary rows are inferences,
    not observations. Stays agent_inference no matter what the
    source claim_types were."""
    conn = _fresh_db()
    _seed_extra(conn, "a_1", claim_type="observation", age_days=30)
    _seed_extra(conn, "a_2", claim_type="user_statement", age_days=31)
    asyncio.run(run_consolidation_pass(
        conn, policy=ConsolidationPolicy(),
        provider=_MockProvider(), audit_chain=_MockChain(), now=NOW,
    ))
    summary_claim = conn.execute(
        "SELECT claim_type FROM memory_entries "
        "WHERE consolidation_state='summary'"
    ).fetchone()[0]
    assert summary_claim == "agent_inference"


def test_runner_summary_row_tagged_with_run_id():
    """Every row touched by the pass — source + summary alike —
    carries the same consolidation_run UUID. Operator pulls
    'show me what run X did' via this column."""
    conn = _fresh_db()
    for i in range(2):
        _seed_extra(conn, f"a_{i}", age_days=30 + i)
    chain = _MockChain()
    result = asyncio.run(run_consolidation_pass(
        conn, policy=ConsolidationPolicy(),
        provider=_MockProvider(), audit_chain=chain, now=NOW,
    ))
    run_ids = {
        r[0] for r in conn.execute(
            "SELECT consolidation_run FROM memory_entries "
            "WHERE consolidation_run IS NOT NULL"
        ).fetchall()
    }
    assert run_ids == {result.run_id}
