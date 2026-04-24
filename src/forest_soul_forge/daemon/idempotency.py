"""X-Idempotency-Key helpers.

ADR-0007 mandates every mutating endpoint honor ``X-Idempotency-Key``.
Semantics:

* Header absent → endpoint executes normally, no caching.
* Header present, first time for that key → execute, then cache the
  response under ``(key, endpoint, request_hash)``.
* Header present, replayed with the same request body → return the
  cached response byte-for-byte (same status code, same JSON).
* Header present, replayed with a *different* request body → 409
  Conflict. That's almost always a client bug (e.g. a UUID being reused
  across two different payloads) and must surface loudly rather than
  silently serve a stale cached result.

The check runs inside the daemon's write lock so two concurrent requests
with the same key can't both execute their handler bodies.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import HTTPException, Request, status

# Header name is fixed by ADR-0007. Keeping it here as a constant so
# callers don't bikeshed the casing.
IDEMPOTENCY_HEADER = "x-idempotency-key"

# Reasonable upper bound — keys are usually UUIDs (36 chars) or random
# tokens. Anything over this is almost certainly a misuse.
MAX_KEY_LENGTH = 200


def get_idempotency_key(request: Request) -> str | None:
    """Extract and sanity-check the idempotency key header.

    Returns ``None`` when the header is absent. Raises 400 when the
    header is present but empty or absurdly long — both cases indicate
    client bugs we'd rather flag than silently accept.
    """
    raw = request.headers.get(IDEMPOTENCY_HEADER)
    if raw is None:
        return None
    key = raw.strip()
    if not key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Idempotency-Key header present but empty",
        )
    if len(key) > MAX_KEY_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"X-Idempotency-Key exceeds {MAX_KEY_LENGTH} chars",
        )
    return key


def compute_request_hash(endpoint: str, body: Any) -> str:
    """SHA-256 over (endpoint || canonical JSON of body).

    The endpoint is folded in so the same key reused across two
    endpoints (another client bug) doesn't accidentally hash-match. JSON
    is serialized with ``sort_keys=True`` so dict ordering doesn't
    produce hash drift across Python versions or clients that serialize
    differently.
    """
    h = hashlib.sha256()
    h.update(endpoint.encode("utf-8"))
    h.update(b"\x00")  # separator byte — endpoint can't contain NUL
    h.update(json.dumps(body, sort_keys=True, default=str).encode("utf-8"))
    return h.hexdigest()
