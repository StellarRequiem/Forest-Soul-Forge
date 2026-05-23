"""``publish_schedule.v1`` — ADR-0088 Phase D publish queue.

Queues a publish request for the operator's forest-publish
connector via ``data/d7/publish_queue.jsonl``. side_effects=external;
every call gates on operator approval via the
``external_always_human_approval`` rule. distribution_pilot
(YELLOW posture) is the only role with this in its kit.

The queue file is read by a future forest-publish connector
(LinkedIn / Twitter / Substack / Ghost / static site) at the
configured fire_at timestamp; this tool ONLY queues — it never
calls a real publish API. The queue → connector handoff is the
load-bearing separation that keeps the agent surface
operator-gated end-to-end.

NEVER publishes directly. Two layers of defense in depth:
  - side_effects=external + per-call approval gate (load-bearing
    regardless of agent posture)
  - distribution_pilot YELLOW posture (auto-queues every non-
    read-only dispatch from the role)

Plus the connector layer (a separate process — when absent, the
publish never fires because nothing reads the queue; the queue
is the operator-visible source of truth either way).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_DEFAULT_PUBLISH_QUEUE = Path("data/d7/publish_queue.jsonl")
_MAX_TITLE_LEN = 500
_MAX_BODY_LEN = 100_000
_MAX_TAGS = 20
_MAX_TAG_LEN = 80

_VALID_PLATFORMS = {
    "twitter", "linkedin", "newsletter", "blog", "substack", "ghost",
}


class PublishScheduleTool:
    """Queue a publish request for the forest-publish connector.

    Args:
      platform (str, required): target publish platform; one of
        twitter / linkedin / newsletter / blog / substack / ghost.
      title (str, required): post title (1..500 chars).
      body (str, required): post body (1..100,000 chars).
      fire_at (str, required): ISO-8601 timestamp when the
        connector should publish. Must be in the future.
      tags (list[str], optional): platform-specific tags (max 20,
        each <= 80 chars).
      reply_to (str, optional): a thread / parent post identifier
        when replying.
      queue_path (str, optional): override queue ledger (tests).

    Output:
      {
        "request_id":   str,
        "platform":     str,
        "fire_at":      str (ISO),
        "queued_at":    str (ISO),
        "queue_path":   str,
      }

    side_effects=external — per-call approval is the load-bearing
    safety. NEVER publishes directly; only queues.
    """

    name = "publish_schedule"
    version = "1"
    side_effects = "external"

    def validate(self, args: dict[str, Any]) -> None:
        platform = args.get("platform")
        if platform not in _VALID_PLATFORMS:
            raise ToolValidationError(
                f"platform must be one of {sorted(_VALID_PLATFORMS)}; "
                f"got {platform!r}"
            )

        title = args.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ToolValidationError(
                "title must be a non-empty string"
            )
        if len(title) > _MAX_TITLE_LEN:
            raise ToolValidationError(
                f"title must be <= {_MAX_TITLE_LEN} chars"
            )

        body = args.get("body")
        if not isinstance(body, str) or not body.strip():
            raise ToolValidationError(
                "body must be a non-empty string"
            )
        if len(body) > _MAX_BODY_LEN:
            raise ToolValidationError(
                f"body must be <= {_MAX_BODY_LEN} chars"
            )

        fire_at = args.get("fire_at")
        if not isinstance(fire_at, str) or not fire_at.strip():
            raise ToolValidationError("fire_at must be a non-empty string")
        try:
            fire_at_dt = _parse_iso(fire_at)
        except ValueError as e:
            raise ToolValidationError(
                f"fire_at not parseable as ISO-8601: {e}"
            )
        if fire_at_dt.timestamp() <= time.time() - 60:
            raise ToolValidationError(
                "fire_at must be in the future "
                "(past timestamps mean the publish would fire immediately, "
                "skipping the operator approval window)"
            )

        tags = args.get("tags", [])
        if not isinstance(tags, list):
            raise ToolValidationError("tags must be a list")
        if len(tags) > _MAX_TAGS:
            raise ToolValidationError(
                f"tags count must be <= {_MAX_TAGS}; got {len(tags)}"
            )
        for i, t in enumerate(tags):
            if not isinstance(t, str):
                raise ToolValidationError(
                    f"tags[{i}] must be a string"
                )
            if len(t) > _MAX_TAG_LEN:
                raise ToolValidationError(
                    f"tags[{i}] must be <= {_MAX_TAG_LEN} chars"
                )

        reply_to = args.get("reply_to")
        if reply_to is not None and not isinstance(reply_to, str):
            raise ToolValidationError("reply_to must be a string")

        qp = args.get("queue_path")
        if qp is not None and not isinstance(qp, str):
            raise ToolValidationError("queue_path must be a string")

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        platform = args["platform"]
        title = args["title"]
        body = args["body"]
        fire_at = args["fire_at"]
        tags = list(args.get("tags") or [])
        reply_to = args.get("reply_to") or ""
        queue_path = Path(
            args.get("queue_path") or _DEFAULT_PUBLISH_QUEUE
        )

        queue_path.parent.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc)
        request_id = _derive_request_id(
            platform, json.dumps(args, sort_keys=True), now.isoformat(),
        )
        record = {
            "request_id": request_id,
            "platform":   platform,
            "title":      title,
            "body":       body,
            "fire_at":    fire_at,
            "tags":       tags,
            "reply_to":   reply_to,
            "queued_at":  now.isoformat(timespec="seconds"),
            "attestor":   ctx.instance_id,
            "agent_role": ctx.role,
            "status":     "pending",
        }
        try:
            with queue_path.open("a") as f:
                f.write(json.dumps(record, sort_keys=True))
                f.write("\n")
        except OSError as e:
            raise ToolValidationError(
                f"could not append to publish queue {queue_path}: {e}"
            )

        out = {
            "request_id": request_id,
            "platform":   platform,
            "fire_at":    fire_at,
            "queued_at":  record["queued_at"],
            "queue_path": str(queue_path),
        }
        return ToolResult(
            output=out,
            metadata={
                "request_id": request_id,
                "platform":   platform,
                "fire_at":    fire_at,
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"queued {platform} publish {request_id} for {fire_at}"
            ),
        )


def _parse_iso(s: str) -> datetime:
    raw = s.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as e:
        raise ValueError(str(e)) from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _derive_request_id(platform: str, blob: str, ts: str) -> str:
    digest = hashlib.sha256(
        f"{platform}|{blob}|{ts}".encode("utf-8"),
    ).hexdigest()
    return f"pub_{digest[:16]}"
