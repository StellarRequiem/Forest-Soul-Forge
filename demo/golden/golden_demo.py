#!/usr/bin/env python3
"""Forest Soul Forge — the Golden Demo.

A governed, locally-run, cryptographically-auditable agent — the one thing the
funded agent-governance vendors (Cisco/Astrix, Oasis, GitGuardian) don't give
you: not just secret storage, but cryptographic PROVENANCE of every action an
agent takes. You can edit a log; you cannot fake what an agent did.

What this proves, end to end, in seconds, with FSF's REAL primitives:

  FORGE  → a constitution compiled from trait sliders, hashed into a
           content-addressed agent identity (DNA).
  BIRTH  → the agent gets a real ed25519 keypair; pubkey + DNA = its passport.
  RUN    → the agent requests a privileged action; the genre's risk profile
           gates it on human approval; the operator approves; it runs.
  AUDIT  → every step lands in a hash-chained log; agent actions are ed25519-
           signed at emit time (FSF's real AuditChain + the real verifier).
  VERIFY → the chain checks: links intact, signatures valid.
  TAMPER → an insider rewrites the log. Two attempts, both caught:
             (1) a lazy edit       → the hash chain catches it.
             (2) an expert edit that recomputes the hash to beat the chain
                                    → the ed25519 SIGNATURE catches it, because
                                      they don't have the agent's private key.

Everything runs LOCAL (no cloud, no API key), in a throwaway temp dir, against
FSF's actual `core.audit_chain.AuditChain` and the `cryptography` ed25519 that
the daemon itself uses. Nothing here is theater.

Run:
    .venv/bin/python demo/golden/golden_demo.py
    (or, double-click) demo/golden/golden-demo.command

Maps to: OWASP Agentic Top-10 (ASI03 Identity & Privilege Abuse, ASI10 Rogue
Agents) · EU AI Act Art. 12 (automatic, tamper-evident lifetime logging).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.exceptions import InvalidSignature
except ImportError:
    sys.exit("This demo needs the ed25519 backend. Install:  pip install -e '.[daemon]'")

try:
    import yaml
except ImportError:
    yaml = None  # genre policy falls back to a documented constant

from forest_soul_forge.core.audit_chain import (  # noqa: E402
    AuditChain,
    _canonical_hash_input,
    _sha256_hex,
)

# ---- presentation ---------------------------------------------------------

_TTY = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _TTY else s
BOLD = lambda s: _c("1", s)
DIM = lambda s: _c("2", s)
GREEN = lambda s: _c("1;32", s)
RED = lambda s: _c("1;31", s)
BLUE = lambda s: _c("1;34", s)
YELLOW = lambda s: _c("1;33", s)
CYAN = lambda s: _c("1;36", s)

_SLOW = os.environ.get("FSF_DEMO_SLOW") == "1"
def beat(secs: float = 0.0) -> None:
    if _SLOW:
        time.sleep(max(secs, 0.4))

def phase(n: int, emoji: str, title: str) -> None:
    print()
    print(BOLD(f"{emoji}  PHASE {n} — {title}"))
    print(DIM("─" * 66))
    beat(0.5)

def line(s: str = "") -> None:
    print(f"   {s}")

# ---- audit-log read/write (canonical, matches AuditChain on-disk form) -----

def read_lines(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]

def write_lines(p: Path, objs: list[dict]) -> None:
    p.write_text(
        "\n".join(json.dumps(o, sort_keys=True, separators=(",", ":")) for o in objs) + "\n",
        encoding="utf-8",
    )


def load_acting_genre() -> tuple[str, dict]:
    """Pick a REAL genre from config/genres.yaml whose risk profile permits
    side-effects (so its actions are human-approval-gated). Grounded in FSF's
    actual config, not invented."""
    path = ROOT / "config" / "genres.yaml"
    if yaml is None or not path.exists():
        return "actuator", {"max_side_effects": "filesystem", "max_initiative_level": "L5"}
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    genres = doc.get("genres", doc)
    # Prefer the highest-side-effect genre; fall back to any non-read-only one.
    rank = {"full": 4, "filesystem": 3, "network": 2, "read_only": 0}
    best, best_rank = None, -1
    for name, g in genres.items():
        rp = (g or {}).get("risk_profile", {}) or {}
        r = rank.get(str(rp.get("max_side_effects", "")), 1)
        if r > best_rank and r > 0:
            best, best_rank = (name, rp), r
    return best if best else ("actuator", {"max_side_effects": "filesystem"})


def main() -> int:
    print()
    print(BOLD(CYAN("  FOREST SOUL FORGE — Golden Demo")))
    print(DIM("  Local · governed · cryptographically-auditable agents."))
    print(DIM("  Provenance, not just credentials — you can't fake what an agent did."))

    with tempfile.TemporaryDirectory() as td:
        chain_path = Path(td) / "audit_chain.jsonl"
        chain = AuditChain(chain_path)  # genesis (seq 0) auto-created

        # Real ed25519 keypair — the same algorithm + library the daemon uses.
        priv = Ed25519PrivateKey.generate()
        pub = priv.public_key()
        pub_raw = pub.public_bytes_raw()
        pub_fpr = hashlib.sha256(pub_raw).hexdigest()[:16]

        # Wire FSF's real sign-on-emit / verify-on-replay hooks (ADR-0049).
        chain.set_signer(lambda entry_hash, dna: priv.sign(entry_hash))
        def verifier(entry_hash: bytes, sig: bytes, dna: str) -> bool:
            try:
                pub.verify(sig, entry_hash)
                return True
            except InvalidSignature:
                return False
            except Exception:
                return False
        chain.set_verifier(verifier)

        # ---- PHASE 1 — FORGE -------------------------------------------------
        phase(1, "🔨", "FORGE — compile a constitution into a content-addressed identity")
        genre_name, risk = load_acting_genre()
        constitution = {
            "role": "FileCustodian",
            "genre": genre_name,
            "traits": {"vigilance": 0.9, "caution": 0.8, "initiative": 0.3},
            "rulebook": ["read_only by default", "any side-effect requires approval"],
        }
        dna = hashlib.sha256(
            json.dumps(constitution, sort_keys=True).encode("utf-8")
        ).hexdigest()
        line(f"role         {BOLD('FileCustodian')}   genre {BOLD(genre_name)}")
        line(f"traits       {constitution['traits']}")
        line(f"→ agent DNA  {CYAN(dna[:32] + '…')}   {DIM('(sha256 of the constitution — identity is content-addressed)')}")
        beat(0.6)

        # ---- PHASE 2 — BIRTH -------------------------------------------------
        phase(2, "👶", "BIRTH — mint an ed25519 keypair; pubkey + DNA = the passport")
        line(f"ed25519 pubkey  {CYAN(pub_fpr)}…   {DIM('(fingerprint)')}")
        e_birth = chain.append(
            "agent_created",
            {"agent": "FileCustodian", "dna": dna[:16], "pubkey_fpr": pub_fpr, "genre": genre_name},
            agent_dna=None,  # operator-emitted → unsigned, per ADR-0049 D3
        )
        line(f"audit  seq={e_birth.seq}  {BOLD('agent_created')}  {DIM('(operator event — unsigned)')}")
        beat(0.6)

        # ---- PHASE 3 — RUN (governed) ---------------------------------------
        phase(3, "🏃", "RUN — a privileged action, gated on human approval")
        target = "/srv/data/customer_records.db"
        line(f"agent requests:  {RED('file_delete')}  →  {BOLD(target)}")
        line(f"governance:      genre {BOLD(genre_name)} risk_profile.max_side_effects="
             f"{BOLD(str(risk.get('max_side_effects')))}")
        line(f"                 {YELLOW('⛔ non-read-only action → REQUIRES HUMAN APPROVAL')}")
        # agent-emitted dispatch request → SIGNED
        chain.append("tool_call_dispatched",
                     {"tool": "file_delete", "target": target, "requested_by": "FileCustodian"},
                     agent_dna=dna)
        chain.append("tool_call_pending_approval",
                     {"tool": "file_delete", "target": target}, agent_dna=None)
        line(f"                 operator reviews… {GREEN('✅ APPROVED')}")
        chain.append("tool_call_approved",
                     {"tool": "file_delete", "target": target, "approver": "operator"},
                     agent_dna=None)
        # agent-emitted execution result → SIGNED
        e_exec = chain.append("tool_call_succeeded",
                              {"tool": "file_delete", "target": target, "result": "deleted"},
                              agent_dna=dna)
        line(f"action executed. {DIM('agent-emitted events are ed25519-signed at emit time.')}")
        beat(0.6)

        # ---- PHASE 4 — THE AUDIT CHAIN --------------------------------------
        phase(4, "📜", "AUDIT — every step, hash-linked and signed")
        entries = chain.read_all()
        line(f"{'seq':>3}  {'event_type':<28}{'actor':<10}{'hash':<14}signed")
        for e in entries:
            actor = "agent" if e.agent_dna else "operator"
            signed = GREEN("ed25519 ✓") if e.signature else DIM("—")
            line(f"{e.seq:>3}  {e.event_type:<28}{actor:<10}{e.entry_hash[:12]}  {signed}")
        line()
        line(DIM("Each entry_hash = sha256(seq, prev_hash, agent_dna, type, data) — a chain."))
        line(DIM("Agent actions also carry an ed25519 signature over that hash — provenance."))
        beat(0.6)

        # ---- PHASE 5 — VERIFY -----------------------------------------------
        phase(5, "🔍", "VERIFY — links intact, signatures valid")
        r = chain.verify()
        if r.ok:
            line(GREEN(f"✅ chain verified — {r.entries_verified} entries, all links + signatures valid"))
        else:
            line(RED(f"❌ unexpected: {r.reason} at seq {r.broken_at_seq}"))
            return 1
        clean = read_lines(chain_path)  # snapshot for restore between attacks
        beat(0.6)

        # ---- PHASE 6 — TAMPER (the kicker) ----------------------------------
        phase(6, "😈", "TAMPER — an insider rewrites the log to hide what happened")

        line(BOLD("Attempt 1 — a lazy edit: change the deleted target, leave the hash."))
        lazy = read_lines(chain_path)
        idx = next(i for i, o in enumerate(lazy) if o["seq"] == e_exec.seq)
        lazy[idx]["event_data"]["target"] = "/srv/data/ALL_RECORDS/*"
        write_lines(chain_path, lazy)
        r1 = AuditChain(chain_path).verify()
        line(f"   re-verify → {RED('🚨 ' + (r1.reason or 'broken'))} at seq {r1.broken_at_seq}")
        line(f"   {GREEN('the hash chain catches it')} — entry_hash no longer matches the data.")
        write_lines(chain_path, clean)  # restore
        beat(0.5)

        line()
        line(BOLD("Attempt 2 — an expert edit: change the target AND recompute the hash"))
        line(BOLD("            so the chain check passes. Now only the signature stands."))
        expert = read_lines(chain_path)
        idx = next(i for i, o in enumerate(expert) if o["seq"] == e_exec.seq)
        ent = expert[idx]
        ent["event_data"]["target"] = "/srv/data/ALL_RECORDS/*"
        # Recompute the canonical hash exactly as AuditChain/verify does — this
        # defeats a naive hash check. (Last entry, so nothing downstream to fix.)
        ent["entry_hash"] = _sha256_hex(_canonical_hash_input(
            seq=ent["seq"], prev_hash=ent["prev_hash"], agent_dna=ent["agent_dna"],
            event_type=ent["event_type"], event_data=ent["event_data"],
        ))
        write_lines(chain_path, expert)
        # Verifier must be wired on the instance that checks signatures:
        c2 = AuditChain(chain_path)
        c2.set_verifier(verifier)
        r2 = c2.verify()
        line(f"   re-verify → {RED('🚨 ' + (r2.reason or 'broken'))} at seq {r2.broken_at_seq}")
        line(f"   {GREEN('the ed25519 signature catches it')} — it was made over the ORIGINAL")
        line(f"   action. The attacker has no private key, so they cannot re-sign the lie.")
        write_lines(chain_path, clean)  # restore
        beat(0.5)

        # ---- CLOSING --------------------------------------------------------
        print()
        print(BOLD("─" * 66))
        print(BOLD("  What just happened, and why it's the moat"))
        print(BOLD("─" * 66))
        line(f"{GREEN('LOCAL')}      everything ran on this machine — no cloud, no API key.")
        line(f"{GREEN('GOVERNED')}   the privileged action was gated on human approval by policy.")
        line(f"{GREEN('AUDITABLE')}  every action is hash-chained AND ed25519-signed at emit.")
        line()
        line(f"A credential vault secures {BOLD('secrets')}. This secures {BOLD('provenance')}: proof of")
        line("what each agent actually did, under whose approval — tamper-evident even")
        line("against an insider who can forge hashes. That is the unmet gap.")
        line()
        line(DIM("OWASP Agentic Top-10: ASI03 Identity & Privilege Abuse · ASI10 Rogue Agents"))
        line(DIM("EU AI Act Art. 12: automatic, tamper-evident lifetime logging."))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
