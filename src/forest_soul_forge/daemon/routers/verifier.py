"""``/verifier/scan`` — on-demand Verifier scan endpoint. ADR-0036 T5.

POST /verifier/scan
  Body: {target_instance_id, max_pairs?, since_iso?, min_confidence?,
         verifier_instance_id}
  Returns: ScanResult JSON (counts + per-pair outcomes)
  Auth: standard X-FSF-Token via require_api_token; require_writes_enabled
        (the scan can write contradiction rows).

The endpoint binds:
  - memory: Memory(registry._conn)
  - classify: async wrapper over the active provider's complete()
  - flagger: closure over memory.flag_contradiction (under the
    daemon's write_lock)

Then runs ``VerifierScan.arun_scan`` and emits a
``verifier_scan_completed`` audit event with the aggregate counts.
The per-pair outcomes ride in the response body for the caller; the
audit-event payload stays bounded in size.

T4 (per-Verifier scheduled-task cron) is deferred — the "existing
scheduled-task surface" the close plan referenced doesn't actually
exist yet; building one is its own substantive work + ADR. v0.3
ships with on-demand only; operators trigger scans manually or via
their own cron / launchd / etc. wrapping `curl /verifier/scan`.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.core.memory import Memory
from forest_soul_forge.daemon.deps import (
    get_active_provider,
    get_audit_chain,
    get_registry,
    get_write_lock,
    require_api_token,
    require_writes_enabled,
)
from forest_soul_forge.daemon.providers import ModelProvider, TaskKind
from forest_soul_forge.verifier.scan import (
    DEFAULT_MAX_PAIRS,
    DEFAULT_MIN_CONFIDENCE,
    PairOutcome,
    ScanResult,
    VerifierScan,
)


router = APIRouter(tags=["verifier"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------
class VerifierScanRequest(BaseModel):
    """Body for POST /verifier/scan.

    ``verifier_instance_id`` is the *Verifier* agent doing the
    scanning — its instance_id lands in detected_by on every flag.
    ``target_instance_id`` is whose memory to scan. Cross-agent
    semantics are out of scope at v0.3; in practice these will
    differ (a Verifier scans some other agent's memory) but the
    endpoint doesn't require it.
    """
    target_instance_id: str = Field(..., min_length=1)
    verifier_instance_id: str = Field(..., min_length=1)
    max_pairs: int = Field(DEFAULT_MAX_PAIRS, ge=1, le=200)
    since_iso: str | None = Field(None)
    min_confidence: float = Field(
        DEFAULT_MIN_CONFIDENCE, ge=0.0, le=1.0,
    )


class PairOutcomeOut(BaseModel):
    """JSON shape of one PairOutcome on the response."""
    earlier_entry_id: str
    later_entry_id: str
    overlap_size: int
    action: str
    contradiction_id: str | None = None
    error: str | None = None
    classification: dict[str, Any] | None = None


class VerifierScanResponse(BaseModel):
    target_instance_id: str
    verifier_instance_id: str
    pairs_considered: int
    pairs_classified: int
    flags_written: int
    low_confidence_skipped: int
    unrelated_skipped: int
    no_contradiction_skipped: int
    errors: int
    outcomes: list[PairOutcomeOut]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@router.post(
    "/verifier/scan",
    response_model=VerifierScanResponse,
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
async def post_verifier_scan(
    request: Request,
    body: VerifierScanRequest,
    registry=Depends(get_registry),
    provider: ModelProvider = Depends(get_active_provider),
    audit: AuditChain = Depends(get_audit_chain),
    write_lock=Depends(get_write_lock),
) -> VerifierScanResponse:
    memory = Memory(conn=registry._conn)  # noqa: SLF001 — internal access by design

    # Async classify: thin wrapper over provider.complete in CLASSIFY
    # mode. provider.complete returns a string directly (verified at
    # /src/forest_soul_forge/daemon/providers/base.py and frontier.py).
    # The Verifier's parser handles prose-prefixed JSON, malformed
    # output, etc., so we don't need to coax the model further here.
    async def classify(prompt: str) -> str:
        result = await provider.complete(
            prompt,
            task_kind=TaskKind.CLASSIFY,
            system=(
                "You are a strict-JSON memory-contradiction classifier. "
                "Respond ONLY with the JSON shape requested in the prompt."
            ),
            max_tokens=600,
        )
        # Provider returns a string. Defensive: if a custom provider
        # returns something else, coerce.
        return result if isinstance(result, str) else str(result or "")

    # Sync flagger: we hold the write_lock for every flag write to
    # preserve single-writer SQLite discipline. The whole scan loop
    # holds the lock for simplicity at v0.3 — scan latency is bounded
    # by max_pairs anyway.
    def flagger(**kwargs):
        return memory.flag_contradiction(**kwargs)

    scan = VerifierScan(
        memory=memory,
        classify=classify,
        flagger=flagger,
        verifier_instance_id=body.verifier_instance_id,
        min_confidence=body.min_confidence,
    )

    # Hold the write lock for the duration of the scan. Reads
    # (find_candidate_pairs, memory.get) and writes (flag_contradiction)
    # both happen inside this section, mirroring the dispatcher's
    # convention.
    with write_lock:
        result = await scan.arun_scan(
            target_instance_id=body.target_instance_id,
            max_pairs=body.max_pairs,
            since_iso=body.since_iso,
        )

        # Emit a verifier_scan_completed audit event. Bounded payload —
        # only counts + the verifier identity + target. Per-pair detail
        # rides in the response, not the chain.
        audit.append(
            "verifier_scan_completed",
            {
                "target_instance_id":      result.target_instance_id,
                "verifier_instance_id":    body.verifier_instance_id,
                "pairs_considered":        result.pairs_considered,
                "pairs_classified":        result.pairs_classified,
                "flags_written":           result.flags_written,
                "low_confidence_skipped":  result.low_confidence_skipped,
                "unrelated_skipped":       result.unrelated_skipped,
                "no_contradiction_skipped": result.no_contradiction_skipped,
                "errors":                  result.errors,
                "min_confidence":          body.min_confidence,
                "max_pairs":               body.max_pairs,
            },
        )

    return _scan_result_to_response(result, body.verifier_instance_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _scan_result_to_response(
    result: ScanResult, verifier_instance_id: str,
) -> VerifierScanResponse:
    return VerifierScanResponse(
        target_instance_id=result.target_instance_id,
        verifier_instance_id=verifier_instance_id,
        pairs_considered=result.pairs_considered,
        pairs_classified=result.pairs_classified,
        flags_written=result.flags_written,
        low_confidence_skipped=result.low_confidence_skipped,
        unrelated_skipped=result.unrelated_skipped,
        no_contradiction_skipped=result.no_contradiction_skipped,
        errors=result.errors,
        outcomes=[_pair_to_out(o) for o in result.outcomes],
    )


def _pair_to_out(o: PairOutcome) -> PairOutcomeOut:
    classification: dict[str, Any] | None = None
    if o.classification is not None:
        classification = {
            "same_topic":    o.classification.same_topic,
            "contradictory": o.classification.contradictory,
            "kind":          o.classification.kind,
            "confidence":    o.classification.confidence,
            "reasoning":     o.classification.reasoning,
        }
    return PairOutcomeOut(
        earlier_entry_id=o.earlier_entry_id,
        later_entry_id=o.later_entry_id,
        overlap_size=o.overlap_size,
        action=o.action,
        contradiction_id=o.contradiction_id,
        error=o.error,
        classification=classification,
    )
