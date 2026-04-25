# Developer tools — recommendations for the Forge dev loop

A curated list of tools that pair well with developing on this codebase. Not every entry is required; pick what fits your workflow. The order is roughly "most useful" → "nice to have."

This doc is for **developer tools** (what you use to build the Forge). For agent tools (what the agents themselves can use), see `tool-risk-guide.md`.

## LM Studio — alternative LLM backend to Ollama

**Verdict:** worth considering if you want a GUI for browsing and managing local models, especially when you're iterating on prompts and want to compare voice quality across model sizes / quantizations. Architecturally already supported (ADR-0008 §"Local provider": *"Same wire format works for LM Studio in server mode"*).

**When to pick LM Studio over Ollama:**
- You want to flip between `qwen2.5-7b`, `mistral-7b-instruct`, `llama3.1-8b-Q4_K_M` vs `Q5_K_M`, etc., without doing `ollama pull` each time.
- You want a one-pane visual on memory usage, model load status, and quantization level.
- You want to download models from the Hugging Face mirror via GUI rather than from Ollama's hosted registry.

**When to stick with Ollama:**
- You want everything reproducible from the docker compose stack — Ollama runs in the `--profile llm` container, LM Studio runs on the host. If your team needs identical env across machines, the in-container path is friendlier.
- You're happy with the model catalog Ollama ships (it's substantial).
- You don't want a desktop app eating menu-bar real estate; the Ollama menu bar agent is lighter.

### Swap path: route the daemon at LM Studio instead of Ollama

LM Studio's local server defaults to **`http://127.0.0.1:1234`** (Ollama defaults to `http://127.0.0.1:11434`).

Steps:

1. **Install LM Studio** — `lmstudio.ai` → download → drag to `/Applications`.
2. **Pull a small model.** In LM Studio's Discover tab, search e.g. `Llama 3.2 1B Instruct` and download a Q4_K_M quantization (~1 GB). Same size class as the `llama3.2:1b` we use with Ollama; same memory footprint inside Docker.
3. **Start the local server.** LM Studio → Developer tab → "Start Server." Note the port (default `1234`) and the exact model identifier LM Studio surfaces (e.g., `meta-llama-3.2-1b-instruct`).
4. **Stop the docker `ollama` container** to free port 11434 and avoid daemon-side healthcheck confusion. From the project root: `docker compose --profile llm stop ollama` (uses the existing `kill-ollama.command` if you also have a brew-installed Ollama running).
5. **Override `FSF_LOCAL_BASE_URL` and the model tags** for the daemon. Edit `.env` in the project root:

   ```
   # Point at LM Studio on the host. The daemon is in a docker container,
   # so it reaches the host via host.docker.internal on Docker Desktop
   # for Mac.
   FSF_LOCAL_BASE_URL=http://host.docker.internal:1234
   FSF_LOCAL_MODEL=meta-llama-3.2-1b-instruct
   ```

   Use whatever model identifier LM Studio's server surfaces in its API at `GET /v1/models`. Mismatched tags fail with a clear "model not found" error from LM Studio, not a silent fallback.

6. **Restart the daemon container** so the new env applies:

   ```
   docker compose up -d --force-recreate daemon
   ```

7. **Verify the chain.** Curl `/runtime/provider`:

   ```
   curl -s http://127.0.0.1:7423/runtime/provider | python3 -m json.tool
   ```

   Look for `"status": "ok"`, `"base_url": "http://host.docker.internal:1234"`, and your model in `"loaded"`. If `"loaded": []`, LM Studio's server isn't actually running or the model isn't loaded into memory there.

8. **Birth a test agent** with `enrich_narrative: true` and read the resulting `## Voice` section. Compare with the `llama3.2:1b`-via-Ollama output captured in `data/soul_generated/VoiceTest__network_watcher_*.soul.md` from prior live-fire runs.

### Things to know

- **LM Studio's OpenAI-compat endpoint** is at `/v1/chat/completions`, while Ollama's native is at `/api/generate`. Our `LocalProvider` calls `/api/generate` by default — which means **LM Studio's native Ollama-compat mode** must be enabled, OR we'd need a frontier-style provider. Check LM Studio's "Server Options" for an "Ollama compatibility" toggle. If it's not there in your version, point `FSF_DEFAULT_PROVIDER=frontier` and use the OpenAI-compat path. (Note: that surfaces the frontier path; ADR-0008's two-deliberate-acts framing applies even though the destination is local.)
- **Performance.** LM Studio tends to ship with newer llama.cpp builds than Ollama at any given moment. If you're benchmarking voice quality / tokens-per-second, run the same model in both and compare.
- **Memory.** Same caveat as Ollama: an 8B model needs ~5 GiB at runtime, which won't fit in Docker Desktop's default ~2 GiB Linux VM allocation. If you bump Docker memory and want to test 8B in both backends, do the bump once and benchmark both.

### Reverting

```
# Remove the LM Studio overrides from .env (or set FSF_LOCAL_BASE_URL back
# to http://ollama:11434), then:
docker compose --profile llm up -d ollama
docker compose up -d --force-recreate daemon
```

The kill-ollama.command remains useful if you go back to brew-installed Ollama and hit the launchd supervisor again.

---

## Other dev tools — brief notes

These are independent of the LM Studio path. Pick what helps your workflow.

### TablePlus or `sqlite3` CLI

For inspecting `data/registry.sqlite` during development. Peek at agent rows, twin sibling indexes, idempotency keys, audit_events table without writing a custom query. TablePlus is paid (free tier exists), `sqlite3` CLI is free and ships with macOS:

```
sqlite3 data/registry.sqlite
sqlite> .schema agents
sqlite> SELECT instance_id, role, sibling_index FROM agents ORDER BY created_at DESC LIMIT 10;
```

### mitmproxy

Sits between the daemon container and the ollama container, captures the exact `/api/generate` payloads + responses. Invaluable when iterating on the voice prompt at the wire level rather than via soul.md output. Setup is more involved (proxy + cert trust), but it pays for itself once.

### HTTPie / `http` CLI

Better than `curl` for poking the daemon's endpoints by hand. `http POST :7423/birth profile:='{"role":"network_watcher","trait_values":{},"domain_weight_overrides":{}}' agent_name=Test` is more readable than the equivalent curl one-liner. Doesn't replace the test harness; useful for ad-hoc checks.

### `entr` — file-watcher for tight feedback loops

`echo src/forest_soul_forge/soul/voice_renderer.py | entr -s 'bash run-tests.command'` reruns the focused test suite on every save. Available via `brew install entr`.

### Jump Desktop / Screens

Already installed. Paired with the Tailscale work, your remote control path for driving the Forge from elsewhere. See the `push-tonight.command` history for context — the first session set this up.

### The Ollama / LM Studio CLI alongside the GUI

LM Studio doesn't have an extensive CLI yet (as of writing). Ollama's CLI is more mature for scripted workflows — `ollama list`, `ollama pull`, `ollama show <model>`. If you keep both Ollama (in container) and LM Studio (on host) installed, the CLI side helps with reproducible testing scripts.

## What we deliberately don't recommend

- **Replit.** Wrong fit for a multi-service docker-compose stack with a 4.7 GB local LLM and an explicit local-first posture (ADR-0008). Useful only as a hosted demo of the frontend-only surface, far down the road. Not for the main dev loop.
- **Cloud IDEs as a primary environment** (GitHub Codespaces, etc.). Same reasoning as Replit. Pairing AI-assisted local IDEs (Claude Code, Cursor) with this codebase is the better combination — you keep local-first posture for the daemon while still getting AI assist on the source.
- **Production observability vendors** (Sentry, Honeycomb, Datadog, etc.). Premature for a single-Mac local-first tool. Revisit if/when there's a deployed instance with multiple users.
- **Frontend frameworks** (React, Vue, Svelte). The frontend is intentionally vanilla JS modules — adding a framework would add build tooling, a bundler, and a new failure mode. Resist the urge until the frontend is genuinely complex enough to need one. We're nowhere near that.

## See also

- `ADR-0008` — local-first model provider (rationale for the swap path's posture).
- `tool-risk-guide.md` — companion guide for **agent** tools (vs. developer tools).
- `live-fire-voice.command` — the existing live-fire harness; reuse its scaffolding when you set up the LM Studio swap.
- `kill-ollama.command` — handles the brew-installed Ollama supervisor if it's still on this Mac when you switch to LM Studio.
