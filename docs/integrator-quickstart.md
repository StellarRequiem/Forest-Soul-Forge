# Integrator quickstart — building against the Forest kernel

ADR-0044 Phase 6 (Burst 131, 2026-05-05).

This is the 30-minute path from "I read the pitch and want to try
it" to "I'm running the kernel, I've exercised the seven ABI
surfaces, I know what conformance failure looks like."

If you complete this and have feedback, that's already the
external integrator validation milestone (ADR-0044 Decision 4
preliminary signal). Tell us what hurts.

## Prerequisites

- Python 3.11 or 3.12 (kernel requirement)
- macOS or Linux (Windows works under WSL but isn't first-class
  yet — let us know if that matters to your use case)
- ~500 MB of disk for the install
- Optional: Docker if you want the containerized path

## Step 1: Bring up the kernel headless (5 minutes)

```bash
git clone https://github.com/StellarRequiem/Forest-Soul-Forge.git
cd Forest-Soul-Forge

pip install -e ".[daemon]"
python -m forest_soul_forge.daemon &
```

The daemon now serves on `127.0.0.1:7423` with no frontend, no
Tauri shell, no SoulUX userspace. This is what you'd integrate
against from outside.

Verify:

```bash
curl -s http://127.0.0.1:7423/healthz | jq '.status'
# Expected: "ok"

curl -s http://127.0.0.1:7423/openapi.json | jq '.info.title'
# Expected: "Forest Soul Forge" or similar
```

If `/healthz` returns "ok", the kernel is up. The full kernel
API (52 endpoints) is at `/docs` (Swagger UI) or
`/openapi.json` (raw spec).

## Step 2: Run the conformance suite (5 minutes)

```bash
pip install ".[conformance]"
pytest tests/conformance/ -v
```

Output will look like:

```
tests/conformance/test_section1_tool_dispatch.py::test_section1_tool_catalog_reachable PASSED
tests/conformance/test_section1_tool_dispatch.py::test_section1_tool_entry_shape PASSED
tests/conformance/test_section1_tool_dispatch.py::test_section1_mcp_call_v1_present PASSED
tests/conformance/test_section1_tool_dispatch.py::test_section1_unknown_tool_refusal_shape PASSED
tests/conformance/test_section2_audit_chain.py::test_section2_jsonl_line_shape PASSED
tests/conformance/test_section2_audit_chain.py::test_section2_seq_monotonic PASSED
tests/conformance/test_section2_audit_chain.py::test_section2_hash_chain_integrity PASSED
... (continues across §3-§7)
```

Every test maps to a section number in the spec
(`docs/spec/kernel-api-v0.6.md`). If any fails, the test docstring
cites which spec subsection it enforces — that's your starting
point for either fixing the kernel or proposing an ADR carve-out.

## Step 3: Try a write endpoint (10 minutes)

The interesting thing about the kernel is that every write is
gated, audited, reversible. Let's exercise that.

### Birth an agent

```bash
# The simplest birth — a network_watcher with default traits.
curl -X POST http://127.0.0.1:7423/birth \
  -H "Content-Type: application/json" \
  -d '{
    "role": "network_watcher",
    "agent_name": "Sentinel-Test",
    "traits": {
      "caution": 85,
      "thoroughness": 80,
      "patience": 75
    }
  }' | jq '.'
```

Save the returned `instance_id` — you'll need it. Verify the
agent landed:

```bash
INSTANCE_ID="<from above>"
curl -s "http://127.0.0.1:7423/agents/$INSTANCE_ID" | jq '.'
```

### Inspect the audit chain

```bash
curl -s "http://127.0.0.1:7423/audit/agent/$INSTANCE_ID" | jq '.events[].event_type'
# Expected: agent_born (and possibly agent_named, audit_chain_init)
```

Every state change is one entry. The `entry_hash` field is
sha256 of canonical-JSON of the entry minus that field — verify
the chain integrity yourself if you want (the conformance suite
already does this in §2.2).

### Set posture

```bash
curl -X POST "http://127.0.0.1:7423/agents/$INSTANCE_ID/posture" \
  -H "Content-Type: application/json" \
  -d '{
    "posture": "yellow",
    "reason": "integrator-quickstart probe"
  }' | jq '.'

# Verify:
curl -s "http://127.0.0.1:7423/agents/$INSTANCE_ID/posture" | jq '.'

# Verify the audit captured it:
curl -s "http://127.0.0.1:7423/audit/agent/$INSTANCE_ID" \
  | jq '.events[] | select(.event_type == "agent_posture_changed")'
```

The posture system (ADR-0045) is one of the seven ABI surfaces.
Yellow means "approval-required for any side-effecting tool";
red means "blanket refuse non-read_only." Try dispatching a tool
on a yellow / red agent and watch it refuse.

## Step 4: Try a plugin grant (10 minutes)

```bash
# List installed plugins (will likely be empty on a fresh install).
curl -s "http://127.0.0.1:7423/plugins" | jq '.plugins | length'
# Expected: 0 or more

# Grant your test agent access to a plugin (this will fail with
# plugin-not-found, which is the expected envelope shape — read it):
curl -X POST "http://127.0.0.1:7423/agents/$INSTANCE_ID/plugin-grants" \
  -H "Content-Type: application/json" \
  -d '{
    "plugin_name": "definitely-not-installed",
    "trust_tier": 2
  }'
# Expected: 404 with detail + code='plugin-not-found' per spec §5.6
```

The error envelope shape is part of the contract. If the kernel
returns a 500 instead of a 404, that's a conformance failure.

## Step 5: Use the CLI (5 minutes)

```bash
fsf --help
# See the documented subcommand tree per spec §6.1

fsf agent posture get $INSTANCE_ID
# CLI version of the curl call above

fsf chronicle per-agent $INSTANCE_ID
# Generate an HTML+MD chronicle of the agent's audit trail
```

## What you've now exercised

In ~30 minutes you've:

- Brought up the kernel without any SoulUX userspace
- Verified all seven ABI surfaces via the conformance suite
- Birthed an agent (constitution + DNA + audit chain entry)
- Set runtime posture (ADR-0045 trust dial)
- Probed the error envelope shape (spec §0.5 + §5.6)
- Used the CLI (spec §6)

That's the integrator surface. From here:

- Build a plugin (see `examples/plugins/forest-echo/` for the
  minimal template, plus `examples/plugins/CONTRIBUTING.md`)
- Build a tool (see `docs/spec/kernel-api-v0.6.md` §1 for the
  Tool Protocol)
- Build a different distribution (replace SoulUX's frontend
  with your own; the kernel doesn't care)
- Run experiments on top of the audit chain
- Propose an ADR if you find an ABI choice you'd change

## Tell us what hurts

The most valuable thing you can do for ADR-0044 Phase 6 is report
back. Specifically:

- Any conformance test that failed, and why (kernel bug vs spec
  underspecification vs your build's own bug)
- Any error message that misled you
- Any ABI choice that surprised you, and what you'd do instead
- Any documentation gap that cost you time

GitHub issues at https://github.com/StellarRequiem/Forest-Soul-Forge/issues — or email alexanderprice91@yahoo.com.

## References

- [`docs/integrator-pitch.md`](integrator-pitch.md) — the 1-pager that probably brought you here
- [`docs/spec/kernel-api-v0.6.md`](spec/kernel-api-v0.6.md) — the 1,042-line ABI spec
- [`tests/conformance/README.md`](../tests/conformance/README.md) — the conformance suite's own usage guide
- [`docs/runbooks/headless-install.md`](runbooks/headless-install.md) — Docker / PyInstaller alternatives
- [`KERNEL.md`](../KERNEL.md) — root-level overview of the seven ABI surfaces
- [`CONTRIBUTING.md`](../CONTRIBUTING.md) — how to contribute back if you want
