# Per-Tool Subprocess Sandbox (ADR-0051) — Operator Runbook

What ADR-0051 ships, when to turn it on, and what to do when
something fires.

---

## What this is

Forest's dispatcher (ADR-0019) historically executed every tool in
the daemon's own Python process. The governance pipeline gates the
dispatch — constitution + genre kit-tier ceiling + posture +
grants + approval queue + per-call counters all have to clear
before the tool runs — but once those gates open, the tool's
Python code executes WITH THE DAEMON'S PRIVILEGES.

That's the trusted-host model (ADR-0025). Acceptable when every
tool was hand-written by the operator. Less acceptable when LLM-
generated tools, MCP plugins, or third-party builtins ship into
agents the operator hasn't audited line-by-line.

ADR-0051 adds an opt-in OS-level boundary underneath the
governance pipeline. When enabled, each eligible tool dispatch
runs in a subprocess sandbox whose profile is derived from the
agent's constitution allowlists. A bug in the tool, or a
supply-chain attack that survives ADR-0062's static IoC scan,
hits the OS boundary before it can damage the host.

**The sandbox is off by default.** The trusted-host model still
holds for operators who accept it.

---

## The three modes

`FSF_TOOL_SANDBOX` env var controls the runtime posture:

| Mode | What it does | When to use |
|---|---|---|
| **off** *(default)* | Tools run in the daemon's process exactly as before. No sandbox-related audit fields beyond `sandbox_mode: "off"` + `sandbox_used: false`. | Trusted-host deployments. Single-operator dev boxes. |
| **strict** | Every eligible tool dispatch runs in a subprocess sandbox. Any sandbox setup failure REFUSES the dispatch (`tool_call_failed` with `sandbox_violation: true`). | Production-hardened deployments. Third-party plugin / LLM-generated tool exposure. |
| **permissive** | Same routing as strict, but falls back to in-process when the sandbox runtime is unavailable or setup fails — with a `sandbox_skipped_reason` annotation in the audit chain so the operator sees the fallback. Sandbox-violations still refuse in both modes. | Mid-rollout staging. Dev work on platforms where bwrap / sandbox-exec isn't installed yet. |

Unrecognized values (typos, blank, anything not in the three
literal strings) fall back to **off** — a typo never silently
escalates a daemon into strict mode.

---

## Per-platform setup

### macOS

Nothing to install. `sandbox-exec(7)` ships with the OS at
`/usr/bin/sandbox-exec`. Verify:

```bash
ls -l /usr/bin/sandbox-exec   # should exist
```

Then set the env var and restart the daemon:

```bash
export FSF_TOOL_SANDBOX=permissive    # or strict
./dev-tools/force-restart-daemon.command
```

### Linux

Install bubblewrap via your distro's package manager:

```bash
# Debian / Ubuntu
sudo apt install bubblewrap

# Fedora / RHEL / Rocky
sudo dnf install bubblewrap

# Arch
sudo pacman -S bubblewrap
```

Verify:

```bash
which bwrap                    # /usr/bin/bwrap
bwrap --bind / / true          # smoke test — should exit 0
```

If the smoke test fails with "user namespaces are not available",
your kernel has user-namespaces disabled. Some hardened RHEL /
CentOS distros do this. Enable via sysctl
(`kernel.unprivileged_userns_clone=1`) or run permissive-mode and
accept the in-process fallback.

Then:

```bash
export FSF_TOOL_SANDBOX=permissive    # or strict
./dev-tools/force-restart-daemon.command
```

### Windows

Not supported in v1 of ADR-0051. Operators on Windows can't run
strict mode; permissive falls back to in-process. A future ADR
will wire AppContainer / Win32 job objects.

---

## What's in the audit chain

Every `tool_call_dispatched` / `_succeeded` / `_failed` event now
carries additive sandbox fields per ADR Decision 6:

| Field | Type | Meaning |
|---|---|---|
| `sandbox_mode` | `"off"\|"strict"\|"permissive"` | Mode the dispatch ran under |
| `sandbox_used` | bool | `true` if the tool actually ran in a subprocess sandbox; `false` for in-process |
| `sandbox_skipped_reason` | string (optional) | Why the sandbox WASN'T used despite mode≠off: `"ineligible"` / `"no_sandbox_on_platform"` / `"setup_failed_permissive_fallback"` |
| `sandbox_violation` | bool (only on failed events) | `true` when the failure was sandbox-side (setup_failed, sandbox_violation, timeout, etc.) |
| `sandbox_error_kind` | string (only on failed events) | The sandbox's own error classification: `setup_failed` / `sandbox_violation` / `timeout` / `unexpected` / `no_sandbox_on_platform` / `result_unpickle_failed` / `result_type_mismatch` |
| `sandbox_stderr` | string (truncated to 1500 chars, only on failed events) | Captured stderr from the sandbox subprocess for forensics |

Pre-ADR readers (older `audit_chain_verify.v1` builds, external
integrators on older spec versions) tolerate unknown fields
gracefully — that's the ADR Decision 6 additive-schema guarantee.

---

## Tools that never sandbox

Five tools opt out of sandbox via `sandbox_eligible: false` in
`config/tool_catalog.yaml`:

| Tool | Why it can't sandbox |
|---|---|
| `memory_recall.v1` | Needs `ctx.memory` + `ctx.agent_registry` — live SQLite handles. |
| `memory_write.v1` | Needs `ctx.memory` + the daemon's write_lock. |
| `memory_disclose.v1` | Needs `ctx.memory` + audit chain emit. |
| `delegate.v1` | Needs `ctx.delegate` — a closure capturing registry + audit + write_lock baked in. |
| `llm_think.v1` | Needs `ctx.provider` — bound LLM provider with HTTP client + credentials + token accounting. |

These continue to run in-process under any mode. Their
`tool_call_succeeded` event carries `sandbox_skipped_reason:
"ineligible"` under non-off modes so the operator sees the gap.

Two test drift-detectors live in `tests/unit/test_tool_catalog.py`:

- `test_real_catalog_marks_memory_delegate_llm_think_ineligible`
  — fails CI if any of the five drops the annotation.
- `test_real_catalog_default_eligibility_for_canonical_sandboxable_tools`
  — fails CI if `audit_chain_verify` / `security_scan` / `code_read`
  get accidentally opted out (they're the canonical sandbox-
  eligible tools).

---

## Monitoring playbook

### Is the sandbox actually firing?

After enabling strict / permissive mode, run a tool that's
sandbox-eligible and check the chain:

```bash
# Run a code_read tool call via the daemon's HTTP API, then:
jq -c 'select(.event_type == "tool_call_succeeded") |
       {seq, tool_key: .event_data.tool_key,
        sandbox_mode: .event_data.sandbox_mode,
        sandbox_used: .event_data.sandbox_used}' \
  examples/audit_chain.jsonl | tail -5
```

You should see `sandbox_mode: "strict"` (or `"permissive"`) and
`sandbox_used: true` on the most recent eligible dispatch. If you
see `sandbox_used: false` + `sandbox_skipped_reason`, that tells
you why — either the tool was ineligible, the platform sandbox
is missing, or permissive fell back from a setup failure.

### Did the sandbox block something?

A real block (a tool tried to write outside its allowed_paths,
say) lands on `tool_call_failed`:

```bash
jq -c 'select(.event_type == "tool_call_failed" and
              .event_data.sandbox_violation == true) |
       {seq, ts: .timestamp, tool_key: .event_data.tool_key,
        kind: .event_data.sandbox_error_kind,
        excerpt: (.event_data.sandbox_stderr // "")[0:200]}' \
  examples/audit_chain.jsonl
```

This is the operator's "is anything trying to break out?" query.
Empty output is healthy. A non-empty result tells you which tool
hit a sandbox boundary, what kind, and the first 200 chars of the
violation message. Cross-reference the `tool_key` against your
agent population — recurring violations from one agent /
DNA-prefix point at a specific compromised or mis-configured
tool.

### Is permissive mode papering over real failures?

```bash
jq -c 'select(.event_data.sandbox_skipped_reason ==
              "setup_failed_permissive_fallback") |
       {seq, ts: .timestamp, tool_key: .event_data.tool_key,
        stderr: (.event_data.sandbox_stderr // "")[0:200]}' \
  examples/audit_chain.jsonl
```

Each entry here is a dispatch that SHOULD have sandboxed but
fell back to in-process because the sandbox runtime errored
during setup. In production this should be zero — non-zero means
your sandbox install is degraded and the operator's running
weaker than they think.

---

## CVE response

When a CVE drops against `sandbox-exec` (rare; Apple ships
patches via point releases of macOS) or `bubblewrap` (more
frequent; package maintainers ship CVE patches via apt/dnf):

1. **Patch your OS first.** The sandbox runtime ships with the
   distro; Forest doesn't bundle either binary.
2. **Restart the daemon.** Forest doesn't reload the sandbox
   binary mid-process; a restart picks up the patched
   `/usr/bin/sandbox-exec` or `/usr/bin/bwrap`.
3. **Switch to permissive mode mid-incident** if the CVE is
   active-exploit-in-the-wild and you suspect the bypass affects
   your fleet. Permissive falls back to in-process while you
   patch; you'll see `setup_failed_permissive_fallback`
   annotations on the chain — that's the cost of not refusing.
4. **Switch back to strict** once patched.

The Forest layer (`tools/sandbox.py` + `_sandbox_worker.py`)
doesn't need a code update for sandbox CVEs — it's a thin shim
over the OS-provided primitive. Only Forest-side bugs in argv
construction or profile generation would require a Forest patch.

---

## Troubleshooting

### `setup_failed` event with "sandbox-exec not found" / "bwrap not found"

The sandbox runtime isn't on this host. See "Per-platform setup"
above. In permissive mode this fallback is silent (audit-only);
in strict mode the dispatch refuses cleanly.

### `setup_failed` with "user namespaces are not available" (Linux)

Kernel has unprivileged user-namespaces disabled. Either:
- Enable: `sudo sysctl kernel.unprivileged_userns_clone=1`
  (Debian/Ubuntu) — make it permanent in `/etc/sysctl.d/`.
- Or accept permissive-mode in-process fallback. Strict mode
  cannot run on this host.

### `result_unpickle_failed` / `result_type_mismatch`

The sandboxed worker exited with success but its stdout couldn't
be parsed as a pickled `ToolResult`. This is a Forest-side bug —
report with the full `sandbox_stderr` excerpt. Workaround:
temporarily mark the offending tool `sandbox_eligible: false` in
the catalog until a patch lands.

### `unexpected` error_kind

Worker crashed for a reason that doesn't match any other class
(import error inside the worker, Python exception caught by the
worker's catchall). The `sandbox_stderr` carries the traceback.
Most often: a tool that references daemon-state handles
(`ctx.memory`, `ctx.provider`, etc.) but isn't annotated
`sandbox_eligible: false`. Fix the catalog annotation.

---

## "Is it working?" smoke test

Three-step procedure for verifying a fresh install:

**Step 1 — sandbox-eligible tool fires under strict mode.**

```bash
export FSF_TOOL_SANDBOX=strict
./dev-tools/force-restart-daemon.command
# Trigger a security_scan.v1 dispatch from any agent.
jq -c 'select(.event_type == "tool_call_succeeded") |
       select(.event_data.tool_key | startswith("security_scan"))
       | .event_data.sandbox_used' examples/audit_chain.jsonl | tail -1
# Expected: true
```

**Step 2 — sandbox-ineligible tool runs in-process with the annotation.**

```bash
# Trigger an llm_think.v1 dispatch.
jq -c 'select(.event_data.tool_key | startswith("llm_think")) |
       .event_data.sandbox_skipped_reason' \
  examples/audit_chain.jsonl | tail -1
# Expected: "ineligible"
```

**Step 3 — a deliberate violation refuses.**

Set up a tool whose `side_effects: filesystem` is dispatched with
a constitution `allowed_paths` that doesn't include `/etc`, then
have the tool try to write `/etc/hosts`. The chain entry:

```bash
jq -c 'select(.event_type == "tool_call_failed" and
              .event_data.sandbox_violation == true) |
       .event_data.sandbox_error_kind' \
  examples/audit_chain.jsonl | tail -1
# Expected: "sandbox_violation"
```

All three should produce the expected output. If any don't, the
sandbox isn't fully wired — re-run the per-platform setup or
file an issue with the `sandbox_stderr` excerpt.

---

## What this ADR does NOT do

Worth re-reading the ADR itself for the full list, but the main
ones to keep in mind operationally:

- **Doesn't sandbox the daemon itself.** The daemon runs
  unsandboxed with full privileges. ADR-0051 sandboxes individual
  TOOL DISPATCHES. A bug in the daemon (FastAPI, governance
  pipeline, etc.) still has full daemon privilege.
- **Doesn't replace the approval gate.** The governance
  pipeline still gates "should this run?"; the sandbox decides
  "if it runs, what can it touch?".
- **Doesn't sandbox memory / delegate / llm_think.** Those need
  direct daemon-state access. Operators see the gap as
  `sandbox_skipped_reason: "ineligible"`.
- **Doesn't formalize forensic capture beyond stderr.** A
  sandbox-violated event logs the stderr; deeper analysis (e.g.
  what syscall was attempted) is operator-side via OS-level
  audit (auditd / OpenBSM).

---

## References

- `docs/decisions/ADR-0051-per-tool-subprocess-sandbox.md` — the ADR.
- `src/forest_soul_forge/tools/sandbox.py` — `Sandbox` Protocol,
  `MacOSSandboxExec`, `LinuxBwrap`, `build_profile`,
  `default_sandbox`.
- `src/forest_soul_forge/tools/sandbox_context.py` —
  `SerializableToolContext` (the pickle-safe projection).
- `src/forest_soul_forge/tools/_sandbox_worker.py` — the
  subprocess entrypoint.
- `src/forest_soul_forge/tools/dispatcher.py` —
  `_execute_tool_maybe_sandboxed`, `_resolve_sandbox_mode`,
  `_lookup_sandbox_eligible`, `SandboxRefused`.
- `tests/unit/test_tool_sandbox.py` — substrate + profile tests.
- `tests/unit/test_tool_catalog.py::TestSandboxEligible` — catalog
  drift detectors.
- `tests/unit/test_tool_dispatcher.py` (sandbox classes at the
  bottom) — dispatcher integration tests covering off / strict /
  permissive flows + the failure shapes.
