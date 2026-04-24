# ADR-0008 — Local-First Model Provider

- **Status:** Accepted
- **Date:** 2026-04-24
- **Supersedes:** —
- **Related:** ADR-0007 (FastAPI daemon), Phase 4 accessibility runtime (pending), Phase 5 medical/therapeutic tier (pending)

## Context

The Forest Soul Forge mission has two co-equal pillars: *protect the user's data* and *understand the user*. The second pillar — accessibility-aware, adaptive, potentially operating as a medical/therapeutic companion with real-time audio/visual interaction — is where this ADR lives.

When an agent carries out a therapy session, interprets a sign-language user turn, or runs a baseline mental/emotional/physical status check, the inputs are:

- live audio and video of the user,
- the user's profile supplied by themselves or a guardian,
- a rolling window of conversational history,
- and often ambient context from peripherals.

Every byte of that is the user's personal state. If inference runs on a hosted frontier API by default, *every* turn leaks that state to a third party. That is not acceptable as a default for a system marketed as "local-first" and "blue-team."

Three shapes for how the daemon talks to a model were considered:

1. **Frontier-only.** Cheapest to ship: one HTTPS client, one API key, one provider. Rejected: violates the core mission. Every interaction becomes a disclosure event. Fails closed only when the network fails, which is the wrong direction for a privacy tool.

2. **Local-only.** No external network. Rejected for a different reason: the open-weights model ecosystem is moving fast, some high-stakes tasks (e.g. constitution generation, safety-critical second opinions) genuinely benefit from a larger hosted model, and forcing users with limited hardware to run a 14B model locally is its own accessibility failure. "Local-only" is too rigid for a tool that is supposed to adapt to the user.

3. **Local-first, frontier-opt-in.** Default path is local. Frontier exists, but is off until the user explicitly turns it on *and* supplies credentials. The user can flip providers from the UI for a specific session; a restart always comes back up on local. This is the shape below.

Option 3 gets the privacy guarantee, keeps the flexibility for power users, and surfaces the trade-off visibly instead of hiding it behind a config knob.

## Decision

The FastAPI daemon (ADR-0007) ships with a **provider abstraction** that has exactly two concrete implementations in v1: `LocalProvider` and `FrontierProvider`. The registry's **default active provider is `local`**, hard-coded, and a fresh daemon restart always lands on `local` regardless of the previous session's choice.

### Provider contract

`ModelProvider` is a small `Protocol` with two methods:

- `async def complete(prompt, *, task_kind, system, max_tokens, **kwargs) -> str`
- `async def healthcheck() -> ProviderHealth`

`TaskKind` is a coarse enum the caller uses to signal *why* we're calling a model: `CLASSIFY`, `GENERATE`, `SAFETY_CHECK`, `CONVERSATION`, `TOOL_USE`. Providers map `TaskKind` to concrete model tags internally. This enables multi-model routing (a 3B model for classify, a 14B for generate, a second small model for safety_check) without the caller knowing anything about model names.

### Local provider

- Target: Ollama-compatible HTTP (`/api/generate` with `stream=false`). Same wire format works for LM Studio's server mode and `llama.cpp` `server` binary.
- Default base URL: `http://127.0.0.1:11434`.
- Default model tag (all task_kinds): `llama3.1:8b`. Chosen as a consumer-hardware workhorse with strong instruction-following and permissive license. Overridable per task_kind via env vars (`FSF_LOCAL_MODEL_CLASSIFY`, etc.).
- Does **not** attempt to start or manage the local server. If Ollama isn't running, `/healthz` reports `UNREACHABLE` and `complete` raises `ProviderUnavailable`. No silent fallback to frontier.
- Healthcheck is cheap: `GET /api/tags`, compares loaded-models list to the configured model tags, reports `DEGRADED` with a `missing: [...]` detail if any configured tag isn't pulled locally.

### Frontier provider

- Target: OpenAI-compatible `/v1/chat/completions`. Covers OpenAI direct, gateway-fronted Anthropic / xAI (LiteLLM etc.), any private gateway.
- **Disabled by default.** Requires both `FSF_FRONTIER_ENABLED=1` and a non-empty `FSF_FRONTIER_API_KEY` before `complete` will return anything. Otherwise `complete` raises `ProviderDisabled`.
- Healthcheck does **not** ping the hosted API — that would burn credits on every `/healthz`. The check reports `DISABLED` or `OK` based on local configuration state only.
- Model tags overridable per task_kind like the local provider.

### Registry state

The provider registry lives in `app.state.providers`. It holds both providers plus the active name. `PUT /runtime/provider` mutates the active name in-process. **No persistence.** A fresh start always reads `default_provider` from config, which defaults to `"local"`. Changing the on-disk default requires editing config, not flipping a button.

### UI contract

The frontend's provider-switch button hits `PUT /runtime/provider`. `GET /runtime/provider` returns active, default, known, and current health so the UI can show a status dot. A flip to `frontier` while it's disabled returns 400 with a clear message — the UI must not silently fall back.

## Consequences

**Upside:**

- The privacy pillar is enforced in code, not documentation. Accidentally leaking a therapy turn to a hosted API requires at least two deliberate acts (enable frontier, supply a key).
- Multi-model routing is first-class from day one, so when Phase 4 runtime lands we don't retrofit the abstraction.
- Developers and tests exercise the local path by default, which means the code paths that matter for real users get the most use.
- The same protocol supports a future third provider (peer-to-peer federated inference, custom-silicon backend, air-gapped model bank) without changing any callers.

**Downside:**

- Users without a local runtime see `/healthz` report `UNREACHABLE` until they install Ollama. We accept that friction — a one-time setup cost for a privacy property the user actually wants.
- Per-task_kind model tuning adds env var surface. Mitigated by having all task_kinds default to the same `llama3.1:8b` tag out of the box; users only touch the fine-grained knobs if they want to.
- "Local-first" is weaker in practice if the user enables frontier globally. We surface the active provider in `/healthz` and in the UI status dot so the choice is visible, but we can't prevent a user from overriding their own privacy posture.

## Open questions

- Should switching to frontier require a confirmation-dialog round-trip beyond the simple PUT? Current shape: no, but the UI can layer that on.
- Should `SAFETY_CHECK` default to a *different* model from `CONVERSATION` even when no env overrides are set? Arguably yes — a second opinion from an identical model is weak. Deferred until we have a real safety-check caller to tune against.
- Do we ship a small curated model-tag catalog with recommended tags per task_kind (and hardware profile) in `config/model_presets.yaml`? Probably yes in Phase 4. Not in v1.
- Streaming: not in the v1 protocol. Add `async def stream_complete(...)` when a concrete caller needs it rather than guessing the shape now.
