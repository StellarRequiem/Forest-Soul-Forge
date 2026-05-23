"""``misconception_log.v1`` — ADR-0089 Phase B misconception ledger.

Appends a misconception record to ``data/d9/misconceptions.jsonl``
keyed by topic_slug + a deterministic misconception_id. Each entry
captures (topic, operator-claimed understanding, ground-truth
correction, severity, source assessment item_id, operator
acknowledgement state). The next assessment session targets the
gap by reading recent ledger entries.

side_effects=filesystem. The actuator-style external classification
isn't right (no network egress) but the ledger drives future
operator-facing behavior, so the per-call human-approval gate is
load-bearing regardless of agent posture (the assessor role is
YELLOW + this tool carries ``requires_human_approval=True`` at the
catalog layer).

ADR-0089 Decision 3 — assessor (YELLOW posture) is the only role
with this in its kit. Same two-layer pattern as time_steward's
schedule_reminder.v1 (filesystem) + posture YELLOW combination.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_DEFAULT_LEDGER_PATH = Path("data/d9/misconceptions.jsonl")
_MAX_TOPIC_LEN = 200
_MAX_TEXT_LEN = 3000
_VALID_SEVERITIES = {"minor", "moderate", "major"}


class MisconceptionLogTool:
    """Append a misconception record to the D9 ledger.

    Args:
      topic_slug (str, required): topic the misconception is about;
        matches an assessment item's topic_slug.
      claim_summary (str, required): the operator's claimed
        understanding, paraphrased for the ledger.
      correction (str, required): the ground-truth correction.
      severity (str, optional): ``minor`` / ``moderate`` / ``major``.
        Default ``moderate``. Drives weight in the assessor's next-
        session targeting.
      source_item_id (str, optional): the assessment item that
        surfaced the misconception. Lets the operator trace from
        ledger back to the scoring event.
      assessor_id (str, optional): the assessor agent instance_id
        responsible; falls back to ctx.instance_id.
      ledger_path (str, optional): override path. Tests pass a
        fixture path; production callers omit.

    Output:
      {
        "misconception_id": str,         # stable derived id
        "topic_slug":       str,
        "severity":         str,
        "logged_at":        str (ISO),
        "ledger_path":      str,
      }
    """

    name = "misconception_log"
    version = "1"
    side_effects = "filesystem"

    def validate(self, args: dict[str, Any]) -> None:
        for k in ("topic_slug", "claim_summary", "correction"):
            v = args.get(k)
            if not isinstance(v, str) or not v.strip():
                raise ToolValidationError(f"{k} is required")
            if k == "topic_slug" and len(v) > _MAX_TOPIC_LEN:
                raise ToolValidationError(
                    f"topic_slug must be <= {_MAX_TOPIC_LEN} chars"
                )
            if k != "topic_slug" and len(v) > _MAX_TEXT_LEN:
                raise ToolValidationError(
                    f"{k} must be <= {_MAX_TEXT_LEN} chars"
                )

        sev = args.get("severity", "moderate")
        if not isinstance(sev, str) or sev not in _VALID_SEVERITIES:
            raise ToolValidationError(
                f"severity must be one of {sorted(_VALID_SEVERITIES)}"
            )

        for k in ("source_item_id", "assessor_id", "ledger_path"):
            v = args.get(k)
            if v is not None and not isinstance(v, str):
                raise ToolValidationError(f"{k} must be a string")

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        topic_slug = args["topic_slug"]
        claim = args["claim_summary"]
        correction = args["correction"]
        severity = args.get("severity") or "moderate"
        source_item_id = args.get("source_item_id") or ""
        assessor_id = args.get("assessor_id") or ctx.instance_id
        ledger_path = Path(
            args.get("ledger_path") or _DEFAULT_LEDGER_PATH
        )

        ledger_path.parent.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc)
        ts = now.isoformat(timespec="seconds")
        misconception_id = _derive_id(
            topic_slug, claim, correction, severity, ts,
        )
        record = {
            "misconception_id":  misconception_id,
            "topic_slug":        topic_slug,
            "claim_summary":     claim,
            "correction":        correction,
            "severity":          severity,
            "source_item_id":    source_item_id,
            "assessor_id":       assessor_id,
            "logged_at":         ts,
            "agent_role":        ctx.role,
        }

        try:
            with ledger_path.open("a") as f:
                f.write(json.dumps(record, sort_keys=True))
                f.write("\n")
        except OSError as e:
            raise ToolValidationError(
                f"could not append to misconception ledger {ledger_path}: {e}"
            )

        body = {
            "misconception_id":  misconception_id,
            "topic_slug":        topic_slug,
            "severity":          severity,
            "logged_at":         ts,
            "ledger_path":       str(ledger_path),
        }
        return ToolResult(
            output=body,
            metadata={
                "misconception_id":  misconception_id,
                "topic_slug":        topic_slug,
                "severity":          severity,
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"logged misconception {misconception_id} on "
                f"{topic_slug!r} ({severity})"
            ),
        )


def _derive_id(
    topic_slug: str, claim: str, correction: str,
    severity: str, ts: str,
) -> str:
    blob = f"{topic_slug}|{claim}|{correction}|{severity}|{ts}"
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return f"mis_{digest[:12]}"
