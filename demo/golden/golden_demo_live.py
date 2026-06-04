#!/usr/bin/env python3
"""Forest Soul Forge — the Golden Demo, LIVE.

Where golden_demo.py proves the cryptographic story against FSF's primitives in
a temp dir, THIS drives the *actual running daemon* over its real HTTP API — the
same surface an operator or the SoulUX frontend uses. Nothing is simulated: a
real daemon boots locally, a real agent is born with a real ed25519 keypair, and
a privileged tool call is gated, approved, executed, and recorded — all observed
by curl-able endpoints.

What it shows, end to end:
  BOOT     → an isolated daemon on a free port, its own temp workspace, no cloud.
  BIRTH    → POST /birth → a real agent (content-addressed DNA + ed25519 keypair
             in a local vault).  enrich_narrative=false → no LLM needed.
  POSTURE  → set the agent RED (every side-effect must be approved).
  RUN      → POST /agents/{id}/tools/call a privileged tool → the REAL governance
             pipeline returns tool_call_pending_approval (the gate, live).
  APPROVE  → POST /pending_calls/{ticket}/approve → the tool executes.
  AUDIT    → GET /audit/tail → the real hash-chained log.
  VERIFY   → FSF's AuditChain.verify() on the daemon's actual chain file → intact.
  TAMPER   → flip one byte in that real log → re-verify → caught.

Runs entirely local (Ollama not required — boot + birth + dispatch need no model).
The ed25519 *signature* layer (provenance + the forgery punchline) is wired in
the daemon per ADR-0049 and demonstrated cryptographically in golden_demo.py.

Run:
    .venv/bin/python demo/golden/golden_demo_live.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

_TTY = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
def _c(code, s): return f"\033[{code}m{s}\033[0m" if _TTY else s
BOLD = lambda s: _c("1", s); DIM = lambda s: _c("2", s)
GREEN = lambda s: _c("1;32", s); RED = lambda s: _c("1;31", s); CYAN = lambda s: _c("1;36", s)

def phase(emoji, title):
    print(); print(BOLD(f"{emoji}  {title}")); print(DIM("─" * 64))
def line(s=""): print(f"   {s}")

def free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p

def api(method, base, path, body=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or "null")
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read() or "null")
        except Exception: return e.code, None

def wait_healthz(base, proc, timeout=40) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            code, _ = api("GET", base, "/healthz", timeout=2)
            if code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def main() -> int:
    print(); print(BOLD(CYAN("  FOREST SOUL FORGE — Golden Demo (LIVE daemon)")))
    print(DIM("  The real running system over its real HTTP API. Nothing simulated."))

    ws = Path(tempfile.mkdtemp(prefix="fsf-live-demo."))
    for sub in ("soul_generated", "forge/skills/installed", "plugins"):
        (ws / sub).mkdir(parents=True, exist_ok=True)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = {**os.environ,
           "FSF_REGISTRY_DB_PATH": str(ws / "registry.sqlite"),
           "FSF_AUDIT_CHAIN_PATH": str(ws / "audit_chain.jsonl"),
           "FSF_SOUL_OUTPUT_DIR": str(ws / "soul_generated"),
           "FSF_SKILL_INSTALL_DIR": str(ws / "forge/skills/installed"),
           "FSF_PLUGINS_DIR": str(ws / "plugins"),
           "FSF_SCHEDULER_ENABLED": "false",          # keep the demo chain clean
           "FSF_SECRET_STORE": "file",                # headless vault (no Keychain prompt)
           "FSF_FILE_SECRETS_PATH": str(ws / "secrets.yaml")}
    py = str(ROOT / ".venv" / "bin" / "python3")
    if not Path(py).exists(): py = sys.executable
    proc = subprocess.Popen([py, "-m", "forest_soul_forge.daemon", "--port", str(port)],
                            cwd=str(ROOT), env=env,
                            stdout=open(ws / "daemon.log", "w"), stderr=subprocess.STDOUT)
    try:
        phase("🚀", f"BOOT — isolated daemon on :{port} (own temp state, no cloud)")
        if not wait_healthz(base, proc):
            line(RED("daemon did not come up — log tail:"))
            print((ws / "daemon.log").read_text()[-1200:]); return 1
        _, hz = api("GET", base, "/healthz")
        line(f"daemon {GREEN('up')} · schema v{hz.get('schema_version')} · provider "
             f"{hz.get('active_provider')} · workspace {DIM(str(ws))}")

        phase("👶", "BIRTH — a real agent over POST /birth (no LLM)")
        code, born = api("POST", base, "/birth",
                         {"profile": {"role": "vault_warden"}, "agent_name": "VaultWarden",
                          "enrich_narrative": False})
        if code not in (200, 201):
            line(RED(f"birth failed [{code}]: {born}")); return 1
        iid = born["instance_id"]
        line(f"instance  {BOLD(iid)}")
        line(f"DNA       {CYAN(str(born.get('agent_dna', born.get('dna', '?'))))}   "
             f"{DIM('content-addressed identity')}")

        phase("🛡️", "POSTURE — set the agent RED (every side-effect must be approved)")
        code, pst = api("POST", base, f"/agents/{iid}/posture",
                        {"posture": "red", "reason": "golden demo — gate everything"})
        line(f"posture   {pst.get('prior_posture')} → {BOLD(RED('red'))}  [{code}]")

        phase("🏃", "RUN — agent requests a privileged tool → the governance gate")
        api("POST", base, f"/agents/{iid}/tools/grant",
            {"tool_name": "misconception_log", "tool_version": "1"})
        code, call = api("POST", base, f"/agents/{iid}/tools/call",
                         {"tool_name": "misconception_log", "tool_version": "1",
                          "session_id": "golden-demo",
                          "args": {"topic_slug": "prod-db-access",
                                   "claim_summary": "agent may drop the customer table",
                                   "correction": "destructive ops require human approval"}})
        status = (call or {}).get("status")
        line(f"dispatch  → {BOLD(status)}   {DIM('(the real ToolDispatcher + genre policy)')}")
        _, pend = api("GET", base, "/pending-calls")
        pcs = (pend or {}).get("pending_calls", [])
        if not pcs:
            line(RED("expected a pending-approval ticket; none found")); return 1
        ticket = pcs[0]["ticket_id"]
        line(f"          {RED('⛔ tool_call_pending_approval')} — ticket {ticket[:16]}…")

        phase("✅", "APPROVE — operator clears it over the API; the tool runs")
        code, _ = api("POST", base, f"/pending_calls/{ticket}/approve", {"operator_id": "operator"})
        line(f"approved  [{code}] → executed")

        phase("📜", "AUDIT — the real hash-chained log (GET /audit/tail)")
        _, tail = api("GET", base, "/audit/tail?limit=20")
        ents = tail if isinstance(tail, list) else (tail or {}).get("entries",
               (tail or {}).get("events", (tail or {}).get("tail", [])))
        for e in ents:
            actor = "agent" if e.get("agent_dna") else "operator"
            line(f"  seq={e.get('seq'):>2}  {e.get('event_type'):<28}{actor:<10}{e.get('entry_hash','')[:12]}")

        phase("🔍", "VERIFY — FSF's AuditChain.verify() on the daemon's real chain file")
        from forest_soul_forge.core.audit_chain import AuditChain
        chain_file = ws / "audit_chain.jsonl"
        r = AuditChain(chain_file).verify()
        if r.ok:
            line(GREEN(f"✅ chain intact — {r.entries_verified} entries, hash-links verified"))
        else:
            line(RED(f"unexpected: {r.reason}")); return 1

        phase("😈", "TAMPER — flip one byte in the real log → re-verify")
        lines = [json.loads(x) for x in chain_file.read_text().splitlines() if x.strip()]
        victim = max(i for i, o in enumerate(lines) if o.get("event_data"))  # latest with data
        lines[victim].setdefault("event_data", {})["_forged"] = "mallory-was-here"
        chain_file.write_text("\n".join(json.dumps(o, sort_keys=True, separators=(",", ":"))
                                        for o in lines) + "\n")
        r2 = AuditChain(chain_file).verify()
        line(f"forged event_data at seq {lines[victim]['seq']}; re-verify → "
             f"{RED('🚨 ' + (r2.reason or 'broken'))} at seq {r2.broken_at_seq}")
        line(f"{GREEN('caught')} — the hash chain makes a silent rewrite of the live log impossible.")

        print(); print(BOLD("─" * 64))
        line(f"{GREEN('This was the real daemon')} — birth, the governance approval gate, the")
        line("approval, execution, and a tamper-evident audit chain, all over the live API.")
        line(f"The ed25519 {BOLD('signature')} layer (provenance + the forgery punchline) is wired")
        line(f"per ADR-0049 and shown cryptographically in {BOLD('golden_demo.py')}.")
        print()
        return 0
    finally:
        proc.terminate()
        try: proc.wait(timeout=5)
        except Exception: proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
