# RFC-FOREST-001 — Synthetic Open-Web Demo Brief

**Status:** Synthetic / Demo only
**Audience:** Forest Soul Forge open-web plane (ADR-003X C8)
**Date:** 2026-04-29

This document is the synthetic input for the C8 open-web demo. A real
RFC would describe a protocol; this one describes itself.

## What this is

The C8 demo proves the open-web chain end-to-end:

1. A `web_researcher`-style agent fetches this document via `web_fetch.v1`
2. The agent persists a brief to `memory_write.v1`
3. The agent delegates to a `web_actuator`-style agent via `delegate.v1`
4. The actuator records "would have called external service X with payload Y"
5. The operator emits a `open_web_demo.simulated_action` ceremony event
   summarizing the chain

## What this is NOT

- A real RFC. Don't cite it.
- A real external action. The actuator simulates; no Linear ticket is created,
  no email is sent, no API is hit beyond the local demo server.
- A general-purpose template. ADR-003X C8 is a smoke test, not the
  shape your production open-web agents should have.

## Why synthetic

The demo runs against a local Python `http.server` spun up by the demo
script on a free port. No external network access. No API keys. No
operator-supplied credentials. This is by design — operators should be
able to run the C8 demo on a fresh install in 30 seconds without touching
config.

## Closing line

If this document was successfully fetched, parsed, and summarized into
memory, then the open-web chain (web_fetch → memory_write → delegate
→ memory_write → ceremony) is wired correctly end-to-end. That's the
deliverable.
