# Forest Soul Forge — MCP connector

Exposes the FSF daemon's **read / analyze / safe-run** surface to any MCP client
(Claude, etc.) over stdio. Read-only + safe by design: no birth, grant, force, or
delete — those stay operator-gated in the dashboard. A thin `httpx` client over
the daemon's HTTP API, so it respects the single-writer daemon and the running
governance.

## Tools

The synaptic layer (ADR-0095) + the task exchange (ADR-0096):

| tool | what |
|---|---|
| `fsf_health` | daemon liveness + posture |
| `fsf_trust` | trust scores per `(agent, problem_class)` (interval + n) |
| `fsf_route` | trust-ranked routing **recommendation** (informs; gated) |
| `fsf_bounties` | what to test next — pairs ranked by trust uncertainty |
| `fsf_quarantined` | confidently-low-trust nodes (release is human-gated) |
| `fsf_verify` | trust-ledger hash-chain integrity |
| `fsf_why` | provenance behind a trust value (audit cross-links) |
| `fsf_nodes` | all tracked nodes + problem_classes |
| `fsf_agents` | the live agent fleet |
| `fsf_training_tasks` | the tiered self-test ladder catalog |
| `fsf_run_training` | run the ladder (deterministic, read-only) → report |

## Install + run

```sh
.venv/bin/pip install mcp                 # FastMCP (httpx is already a core dep)
PYTHONPATH=. .venv/bin/python -m mcp_connector.server
```

## Wire into an MCP client (`.mcp.json`)

```json
{
  "mcpServers": {
    "fsf": {
      "command": "/abs/path/Forest-Soul-Forge/.venv/bin/python",
      "args": ["-m", "mcp_connector.server"],
      "env": { "PYTHONPATH": "/abs/path/Forest-Soul-Forge" }
    }
  }
}
```

Config via env: `FSF_DAEMON_URL` (default `http://127.0.0.1:7423`),
`FSF_API_TOKEN` (optional bearer), `FSF_MCP_TIMEOUT_S` (default 30).
