# Web research demo (ADR-003X C8)

The mirror of Phase E1 for the open-web plane. Proves the chain
`web_fetch → memory_write → delegate → memory_write → ceremony` is
wired end-to-end without requiring external network access, API keys,
or operator config.

## How to run

Double-click `web-research-demo.command` from Finder, or:

```bash
./web-research-demo.command
```

The script:
1. Confirms the daemon is reachable
2. Spins up a local Python `http.server` on a free port serving
   `synthetic_rfc.md` (no external network)
3. Births a research agent and an actuator agent (operator_companion
   role with `tools_add: [web_fetch, memory_write, delegate]`)
4. Patches the research agent's constitution to allowlist 127.0.0.1
   for `web_fetch.v1`
5. Calls `web_research_brief.v1` on the researcher (fetches RFC,
   persists brief, delegates to actuator)
6. Calls `web_actuator_handoff.v1` on the actuator (persists "would
   have called" memory entry)
7. Emits a `open_web_demo.simulated_action` ceremony event
8. Renders a chronicle of the chain into `data/chronicles/`
9. Cleanup: archives the test agents, kills the local HTTP server

## Why "synthetic"

Real-world open-web tasks (ticket creation, status-page polling,
RFC lookups) need credentials, allowlisted hosts, and operator-curated
MCP servers. The demo is intentionally narrower: it proves the
machinery works without requiring any of that. Once the demo is green
on a fresh install, the operator can:

- Replace `synthetic_rfc.md` with a real URL on a host they allowlist
- Swap the simulated actuator step for a real `mcp_call.v1` against
  an MCP server in `config/mcp_servers.yaml`
- Add credentials via the per-agent secrets store (G2)

## What success looks like

The chronicle render at the end of the script shows, in order:

```
agent_created          web_researcher
agent_created          web_actuator
skill_invoked          web_research_brief
tool_call_dispatched   web_fetch.v1            (fetches synthetic RFC from 127.0.0.1)
tool_call_succeeded
tool_call_dispatched   memory_write.v1         (persists brief)
tool_call_succeeded
tool_call_dispatched   delegate.v1             (handoff to actuator)
agent_delegated        researcher → actuator
skill_invoked          web_actuator_handoff
tool_call_dispatched   memory_write.v1         (persists would-be action)
tool_call_succeeded
skill_completed        web_actuator_handoff
skill_completed        web_research_brief
ceremony               open_web_demo.simulated_action
agent_archived         web_researcher
agent_archived         web_actuator
```

If you see all of those, ADR-003X C8 is closed.
